"""Turning one job posting into one tailored CV. The port of `pipeline/cv_builder.py`'s middle.

**Python does the mechanics, Claude makes one call.** The reference instance measured the
alternative: an agentic build re-reading the profile, the skills and the template every turn, where
that loop rather than the writing was most of the cost. Here the renderer fills the base document
(`libs.templates.cv`), this module hands it to the model with the posting, and the model returns a
tailored copy of it.

**Three things are enforced mechanically after generation, not asked for.** Every one of them is a
rule the prompt already states and the model still breaks intermittently, and asking twice is not a
control:

- **The CSS is re-injected from the template.** It is stripped before sending — the model only
  changes text — and restored byte-for-byte after. So the layout, the page rules and the font
  family are always the platform's, whatever came back.
- **Em-dashes are stripped from prose and bullets.** They are the model's default habit and the
  prompt's most-broken rule. Entry titles keep theirs, where the prompt allows them.
- **Hidden content is scanned for.** Zero-width characters and inline hiding styles in the body.
  Keyword-stuffing a CV with invisible text is a thing people do to ATS systems, and a document
  that does it under a user's name is a document that gets them blacklisted, not shortlisted.

**Nothing here writes a file or touches the queue.** It takes strings and returns strings, so the
whole tailoring path is testable without Chrome, without Postgres and without spending anybody's
credential.
"""
from __future__ import annotations

import logging
import re

from libs.contracts.profile import ProfileSchema
from libs.prompts import TAILOR_CV_TEMPLATE, load
from libs.scoring.docprompt import render_cover_prompt, render_cv_prompt
from libs.templates import BASE_CV_TEMPLATE
from libs.templates import load as load_template
from libs.templates.cv import render_cv

logger = logging.getLogger(__name__)

# Haiku, deliberately. `MODEL_SELECTION_RESEARCH.md` §5's verdict for writing is "stay on Claude,
# cost-optimise within it": Haiku for the single-shot build, Sonnet only when an A/B on real outputs
# justifies the ~20x. The user pays for this call, so the default has to be the one that is cheap
# enough to press twice.
CV_MODEL = "claude-haiku-4-5"

# Enough for a two-page CV with room to spare. A truncated document is not a shorter CV — it is an
# HTML fragment that will not compile.
MAX_TOKENS = 16_000

# 4096 measured better on writing quality than the adaptive default (2026-07-20, reference
# instance). `None` would mean adaptive; `0` disables thinking, which measured ~8x faster and
# visibly worse — see `libs/llm/client.py`.
THINKING_TOKENS = 4_096

# The posting, capped. The full description is what makes tailoring specific rather than generic,
# so this is deliberately far above the labeller's 2,500: there, the tail is EEO boilerplate nobody
# scores on; here, a requirement buried at the bottom is exactly what the CV has to answer.
JD_MAX_CHARS = 6_000

_HTML = re.compile(r"(?is)(<!doctype html.*?</html>|<html\b.*?</html>)")
_STYLE = re.compile(r"(?is)<style\b.*?</style>")
_EMDASH = re.compile(r"\s*(?:—|&mdash;|&#8212;)\s*")

# Invisible characters and the inline styles that hide text. Scanned in the body only — the
# canonical CSS is the platform's own and contains legitimate print rules.
#
# Written as escapes rather than as the characters themselves, which is not a style preference: a
# literal class of invisible characters is a line no reviewer can read, in the one place where
# reading it is the whole point. The ranges are the zero-width and bidirectional-control blocks —
# spaces and joiners, the LTR/RTL overrides, the word joiner and its neighbours, and the BOM.
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u202a-\u202e]")
_HIDE_STYLE = re.compile(
    r"(?is)(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?!\.)|"
    r"font-size\s*:\s*0(?:px|pt)?\b|color\s*:\s*#?f{3,6}\b|text-indent\s*:\s*-\d{3,}|"
    r"left\s*:\s*-\d{3,})")


class BuildFailed(RuntimeError):
    """The model did not return a usable document. Retryable; the worker decides."""


def base_document(profile: ProfileSchema) -> str:
    """**Deprecated — the base CV is now a per-user file** (`libs/profiles`, `base_cv.html`).

    Kept only until the schema machinery is deleted in the last phase of `REBUILD_PLAN.md`; the
    tests that still exercise the renderer call it. A caller wanting the document a user actually
    applies with should read their `base_cv.html` instead: the setup agent writes their real
    experience into it, where this can only emit what a schema has fields for.
    """
    return render_cv(profile)


def job_context(job, *, score: float | None = None, description: str | None = None,
                fit_notes: str | None = None, salary_bands: dict | None = None) -> str:
    """What the model is told about the posting — the reference instance's context, ported whole.

    **This was trimmed to four lines once and the CVs got worse.** The reasoning was that salary
    intel and assessment notes are context for a human reading a log; that was wrong. A model given
    only a description matches keywords against it. A model told *what we already concluded about
    this role and why it fits this person* has an angle to write toward, which is the difference
    between a document that reads as tailored and one that reads as search-and-replace.

    `fit_notes` is the scoring-time read, written by the labeller into `user_labels.rationale` —
    fifteen words on what the role is and why it matched.
    """
    lines = [f"Title: {job.title or ''}",
             f"Company: {job.company or ''}",
             f"Location: {job.location or ''}",
             f"Source: {job.source or ''}",
             f"URL: {job.url}"]
    if getattr(job, "application_url", None):
        lines.append(f"Application URL: {job.application_url}")
    if score is not None:
        lines.append(f"Model fit score: {score:.0f}")

    posted = _age(getattr(job, "posted_at", None))
    if posted:
        lines.append(f"Posted: {posted}")
    if getattr(job, "applicants", None) is not None:
        # How contested it is. A role with two hundred applicants deserves a sharper opening than
        # one with six, and the model can only weigh that if it is told.
        lines.append(f"Applicants: {job.applicants}")
    if getattr(job, "workplace", None):
        lines.append(f"Workplace: {job.workplace}")

    text = (description if description is not None else job.description) or ""
    lines.extend(_salary_lines(job, text, salary_bands))

    if fit_notes:
        lines += ["", "=== What we understand (our assessment) ===", fit_notes]

    text = text.strip() or "(no description was captured for this posting)"
    if len(text) > JD_MAX_CHARS:
        text = text[:JD_MAX_CHARS] + f"\n[truncated to {JD_MAX_CHARS} chars]"
    return "\n".join([*lines, "", "=== Job description ===", text])


def _salary_lines(job, description: str, bands: dict | None) -> list[str]:
    """What the posting pays and what this candidate should be asking for.

    Both, because they answer different questions: the first is a fact about the role, the second
    is the do-not-undersell position the reference instance takes deliberately. A gap between them
    is information a cover letter may need, not an error.
    """
    from libs.corpus import salary as salarylib  # noqa: PLC0415 - one caller, kept local

    payload = {"title": job.title or "", "location": job.location or "",
               "description": description, "source": job.source or ""}
    posted = salarylib.extract(description)
    ask = salarylib.suggest(payload, posted, bands)

    out = []
    if posted:
        out.append(f"Posted salary: {salarylib.fmt(posted)}")
    if ask:
        below = "  (posting below target)" if ask.get("below_target") else ""
        out.append(f"Target ask band: {salarylib.fmt(ask)}{below}")
    return out


def _age(posted_at) -> str:
    """`3d`, `<1h`, or empty. Freshness changes how much effort a posting is worth."""
    from libs.corpus.timefilter import age_label  # noqa: PLC0415 - one caller

    if posted_at is None:
        return ""
    return age_label(posted_at.isoformat() if hasattr(posted_at, "isoformat") else str(posted_at))



def build_prompt(profile: ProfileSchema, base_html: str, context: str, *,
                 template: str | None = None, feedback: str | None = None) -> str:
    """The SACRED prompt, filled: the candidate, the job, and the base document.

    **`template` is the user's own `tailor_cv.md`.** They ship field-agnostic and get tuned per
    person — `/setup-prompts` calls it "a short interview to set the handful of things that
    genuinely differ". Somebody who has tuned theirs over months keeps it; a new account starts
    from the platform's.

    `render_cv_prompt` fills the candidate placeholders on a generalised copy. A copy that already
    names its subject has none to fill and passes through untouched, which is what makes importing
    a hand-tuned prompt a straight copy rather than a conversion.
    """
    prompt = (render_cv_prompt(profile, template=template)
              .replace("<JOB_CONTEXT>", context)
              .replace("<BASE_TEMPLATE>", base_html))
    if feedback:
        prompt += f"\n\nREVISION NEEDED: {feedback}\nReturn the full corrected HTML document."
    return prompt


def tailor(client, profile: ProfileSchema, base_html: str, context: str, *,
           facts_md: str | None = None, feedback: str | None = None, template: str | None = None,
           model: str = CV_MODEL) -> str:
    """One call. Returns the tailored document with the platform's own CSS restored.

    **The facts block is the system prompt**, and that is the single most important line in this
    module. The reference instance measured what happens without its equivalent: stripped of profile
    grounding, the model invented an employer and fabricated metrics. In a hosted product that lands
    on a real application under a real person's name (`libs/scoring/facts.py`).
    """
    stripped, style = strip_style(base_html)
    prompt = build_prompt(profile, strip_comments(stripped), context, template=template,
                          feedback=feedback)

    reply = client.complete_text(
        model=model,
        system=facts_md or _MINIMAL_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS,
        thinking_tokens=THINKING_TOKENS)

    html = extract_html(reply)
    if html is None:
        # Never log the reply: it is the candidate's CV. The length is what distinguishes "refused"
        # from "wrote prose instead of HTML" from "truncated", which is all triage needs.
        logger.error("the tailor call returned %d chars with no HTML document in it", len(reply))
        raise BuildFailed("the model did not return an HTML document")

    return sanitise(restore_style(html, style))


# Used only when a user somehow has no rendered facts block. Not a fallback anybody should reach:
# `TenantRepository.save_profile` renders one whenever a profile is confirmed.
_MINIMAL_SYSTEM = ("Write only what the base template below already states about the candidate. "
                   "Never invent an employer, a date, a metric, or a technology.")


# ── the cover letter ──────────────────────────────────────────────────────────────────────────

# Chars of plain-text CV sent to the cover prompt. The reference's number: the cover model needs the
# candidate's facts, not the markup, and sending the full HTML costs ~1,000–1,500 tokens for nothing.
CV_TEXT_MAX = 1_500


def tailor_cover(client, profile: ProfileSchema, cv_html: str, context: str, *,
                 facts_md: str | None = None, template: str | None = None,
                 model: str = CV_MODEL) -> str:
    """The cover letter for one job, built from the CV that was just tailored for it.

    **The CV goes in as plain text, not markup** — the reference's own decision, for the reason
    above. The head block goes in as-is because the prompt asks for it byte-for-byte: the letter is
    a second document that has to print with the same fonts and the same A4 rules as the CV, and it
    is the only part of the layout the model is trusted to carry.
    """
    head = head_block(cv_html)
    prompt = (render_cover_prompt(profile, template=template)
              .replace("<HEAD_BLOCK>", head)
              .replace("<JOB_CONTEXT>", context)
              .replace("<CV_HTML>", html_to_text(cv_html)))

    reply = client.complete_text(
        model=model,
        system=facts_md or _MINIMAL_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS,
        thinking_tokens=THINKING_TOKENS)

    letter = extract_html(reply)
    if letter is None:
        logger.error("the cover call returned %d chars with no HTML document in it", len(reply))
        raise BuildFailed("the model did not return an HTML document")

    # **Em-dashes come out of the whole letter, not just its bullets.** A cover letter is prose end
    # to end, so there is no entry-title header where one is legitimate — the reference strips them
    # everywhere except the trusted style block, and so does this.
    return strip_hidden(_strip_cover_emdashes(letter))


def head_block(document: str) -> str:
    """The `<head>` of the CV template, which is what carries the fonts and the page rules."""
    match = re.search(r"(?is)<head\b.*?</head>", document) or _STYLE.search(document)
    return match.group(0) if match else ""


def html_to_text(document: str, max_chars: int = CV_TEXT_MAX) -> str:
    """Tags out, whitespace collapsed. The CV as facts, for the cover prompt."""
    text = re.sub(r"<[^>]+>", " ", _STYLE.sub(" ", document))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _strip_cover_emdashes(letter: str) -> str:
    match = _STYLE.search(letter)
    if not match:
        return _EMDASH.sub(", ", letter)
    return (_EMDASH.sub(", ", letter[:match.start()]) + match.group(0)
            + _EMDASH.sub(", ", letter[match.end():]))


def sanitise(html: str) -> str:
    """The mechanical enforcement of the rules the prompt states and the model breaks.

    Removal, then `scan_hidden` verifies. Two steps rather than one because they answer different
    questions: this strips what can be stripped safely, and the scan says whether anything invisible
    is left — and if something is, the caller refuses to deliver rather than shipping a document
    that could get somebody blacklisted by an ATS.
    """
    return strip_hidden(strip_prose_emdashes(html))


def strip_hidden(html: str) -> str:
    """Zero-width characters out, and any inline `style` attribute that hides text.

    Safe to do mechanically because the platform's own template has no inline styles at all: every
    one of them arrived in the model's output, so removing the hiding ones takes nothing the layout
    depends on. The `<style>` block is left alone — it is restored from the template anyway.
    """
    style = _STYLE.search(html)
    if style:
        head, block, tail = html[:style.start()], style.group(0), html[style.end():]
        return _clean_body(head) + block + _clean_body(tail)
    return _clean_body(html)


def _clean_body(fragment: str) -> str:
    fragment = _ZERO_WIDTH.sub("", fragment)
    return re.sub(r'(?i)\s*style="[^"]*"',
                  lambda match: "" if _HIDE_STYLE.search(match.group(0)) else match.group(0),
                  fragment)


def strip_style(html: str) -> tuple[str, str]:
    """Remove the `<style>` block before sending, and hand it back for restoration.

    Saves ~700 tokens each way, and the prompt is told CSS is managed externally. The saving is the
    lesser reason: the point is that the model cannot change the layout even if it tries.
    """
    match = _STYLE.search(html)
    if not match:
        return html, ""
    return html[:match.start()] + html[match.end():], match.group(0)


def strip_comments(html: str) -> str:
    """Drop the template's HTML comments before sending.

    They are notes to whoever maintains the layout — which markers exist, why the page rules are
    load-bearing, that the file is data rather than a document. To the model they are tokens the
    user pays for and instructions aimed at somebody else, and "the CSS below is load-bearing" is
    exactly the sort of line that invites it to reason about CSS it was told not to touch.
    """
    return re.sub(r"(?s)<!--.*?-->", "", html)


def restore_style(tailored: str, style: str) -> str:
    """Re-inject the canonical CSS, replacing whatever the model emitted.

    **The last branch used to return the document unstyled, and that shipped.** Told the CSS is
    managed externally, the model sometimes returns `<html><body>…` with no `<head>` at all — and
    with no `</head>` to inject into, this handed back a document with no stylesheet. Chrome then
    rendered it at browser defaults: no A4 page rule, no `@page` margin, so it printed its own
    header and footer over the page, and the first real CV a user received was an unstyled sheet of
    text with a file path along the bottom.

    It failed silently because every other check still passed — the HTML was valid, the text was
    correct, the PDF compiled, and it was one page. So the head is now *built* when it is missing
    rather than skipped, and `tests/test_docgen.py` asserts the stylesheet survives every shape of
    reply.
    """
    if not style:
        return tailored

    match = _STYLE.search(tailored)
    if match:
        return tailored[:match.start()] + style + tailored[match.end():]

    head = tailored.find("</head>")
    if head != -1:
        return tailored[:head] + style + "\n" + tailored[head:]

    body = re.search(r"(?i)<body\b[^>]*>", tailored)
    if body:
        return tailored[:body.start()] + f"<head>\n{style}\n</head>\n" + tailored[body.start():]

    # No head, no body: whatever came back is the content. Wrapping it is the only way the layout
    # survives, and a document without the platform's CSS must never reach a PDF.
    return ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
            f"{style}\n</head>\n<body>\n{tailored}\n</body>\n</html>")


def extract_html(reply: str) -> str | None:
    """The document out of the reply, ignoring any commentary around it."""
    if not reply:
        return None
    match = _HTML.search(reply)
    return match.group(1) if match else None


def strip_prose_emdashes(html: str) -> str:
    """Em-dashes out of bullets and the profile paragraph, left alone in entry titles.

    The prompt bans them in prose and allows them in an entry's title header, and the model ignores
    that intermittently because em-dashes are its default habit. Its self-check is aspirational; the
    regex is not.
    """
    def fix(match: re.Match[str]) -> str:
        return _EMDASH.sub(", ", match.group(0))

    html = re.sub(r"(?is)<li\b[^>]*>.*?</li>", fix, html)
    return re.sub(r'(?is)<p\b[^>]*class="[^"]*profile[^"]*"[^>]*>.*?</p>', fix, html)


def scan_hidden(html: str) -> list[str]:
    """Invisible content in the body. Empty list means clean.

    Runs on every build because the failure is undetectable by looking at the PDF — which is the
    entire point of hidden text, and why a human reviewing the output is not a control.
    """
    body = _STYLE.sub("", html)
    issues: list[str] = []

    invisible = len(_ZERO_WIDTH.findall(body))
    if invisible:
        issues.append(f"{invisible} invisible/zero-width character(s) in body text")

    hidden = sorted({match.group(1).split(":")[0].strip().lower()
                     for match in _HIDE_STYLE.finditer(body)})
    if hidden:
        issues.append("inline hiding style(s): " + ", ".join(hidden))
    return issues


def page_feedback(pages: int | None, *, maximum: int = 2) -> str | None:
    """What to tell the model when the PDF came out too long, or nothing when it did not.

    One page is the goal and two is the ceiling. The instruction says *cut*, never *shrink*: a model
    told a document is too long will otherwise reduce the font size, which produces a CV that fits
    and is unreadable — and the CSS is restored from the template afterwards anyway, so the attempt
    would be silently undone and the length unchanged.
    """
    if pages is None or pages <= maximum:
        return None
    return (f"Your CV compiled to {pages} pages. It must not exceed {maximum} pages of A4, and one "
            f"sharp page is better than two. Cut the least-relevant entries and bullets until it "
            f"fits. Do not shrink the font, the padding or the margins, and do not pad what "
            f"remains.")


def template_base() -> str:
    """The unfilled template. Only for callers that need the layout without a profile."""
    return load_template(BASE_CV_TEMPLATE)


def prompt_text() -> str:
    """The SACRED CV prompt, unfilled. Exposed so a test can assert what reaches the model."""
    return load(TAILOR_CV_TEMPLATE)
