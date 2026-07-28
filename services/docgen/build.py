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
from libs.scoring.docprompt import render_cv_prompt
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
    """The candidate's full CV, before tailoring — the input the prompt calls BASE TEMPLATE."""
    return render_cv(profile)


def job_context(job, *, score: float | None = None, description: str | None = None) -> str:
    """What the model is told about the posting.

    Deliberately short. The reference instance's version carried salary intel, fit breakdowns,
    opportunity scores and its own assessment notes; most of that is context for a human reading a
    log, and every line of it is tokens the user pays for on a document that is written from the
    posting and the CV.
    """
    lines = [f"Title: {job.title or ''}",
             f"Company: {job.company or ''}",
             f"Location: {job.location or ''}",
             f"URL: {job.url}"]
    if score is not None:
        lines.append(f"Model fit score: {score:.0f}")

    text = (description if description is not None else job.description) or ""
    text = text.strip() or "(no description was captured for this posting)"
    if len(text) > JD_MAX_CHARS:
        text = text[:JD_MAX_CHARS] + f"\n[truncated to {JD_MAX_CHARS} chars]"
    return "\n".join([*lines, "", "=== Job description ===", text])


def build_prompt(profile: ProfileSchema, base_html: str, context: str, *,
                 feedback: str | None = None) -> str:
    """The SACRED prompt, filled: the candidate from the profile, the job, the base document.

    `render_cv_prompt` fills the candidate placeholders — name, languages, eligibility — and this
    fills the two the caller owns. Keeping that split means the identity rules have exactly one
    implementation, shared with the cover letter.
    """
    prompt = (render_cv_prompt(profile)
              .replace("<JOB_CONTEXT>", context)
              .replace("<BASE_TEMPLATE>", base_html))
    if feedback:
        prompt += f"\n\nREVISION NEEDED: {feedback}\nReturn the full corrected HTML document."
    return prompt


def tailor(client, profile: ProfileSchema, base_html: str, context: str, *,
           facts_md: str | None = None, feedback: str | None = None,
           model: str = CV_MODEL) -> str:
    """One call. Returns the tailored document with the platform's own CSS restored.

    **The facts block is the system prompt**, and that is the single most important line in this
    module. The reference instance measured what happens without its equivalent: stripped of profile
    grounding, the model invented an employer and fabricated metrics. In a hosted product that lands
    on a real application under a real person's name (`libs/scoring/facts.py`).
    """
    stripped, style = strip_style(base_html)
    prompt = build_prompt(profile, strip_comments(stripped), context, feedback=feedback)

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
    """Re-inject the canonical CSS, replacing whatever the model emitted."""
    if not style:
        return tailored
    match = _STYLE.search(tailored)
    if match:
        return tailored[:match.start()] + style + tailored[match.end():]
    head = tailored.find("</head>")
    if head != -1:
        return tailored[:head] + style + "\n" + tailored[head:]
    return tailored


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
