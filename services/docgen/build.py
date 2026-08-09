"""Turning one job posting into one tailored CV."""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from libs.contracts.profile import ProfileSchema
from libs.profiles import PLACEHOLDER_MARKERS
from libs.scoring.docprompt import render_cover_prompt, render_cv_prompt
from libs.templates import BASE_CV_TEMPLATE
from libs.templates import load as load_template

logger = logging.getLogger(__name__)

# Haiku, deliberately.
CV_MODEL = "claude-haiku-4-5"

# Enough for a two-page CV with room to spare.
MAX_TOKENS = 32_000

# 4096 measured better on writing quality than the adaptive default (2026-07-20, reference instance).
THINKING_TOKENS = 4_096

# The posting, capped.
JD_MAX_CHARS = 6_000

_HTML = re.compile(r"(?is)(<!doctype html.*?</html>|<html\b.*?</html>)")
_STYLE = re.compile(r"(?is)<style\b.*?</style>")
_EMDASH = re.compile(r"\s*(?:—|&mdash;|&#8212;)\s*")

# Invisible characters and the inline styles that hide text.
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u202a-\u202e]")
_HIDE_STYLE = re.compile(
    r"(?is)(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?!\.)|"
    r"font-size\s*:\s*0(?:px|pt)?\b|color\s*:\s*#?f{3,6}\b|text-indent\s*:\s*-\d{3,}|"
    r"left\s*:\s*-\d{3,})")


class BuildFailed(RuntimeError):
    """The model did not return a usable document. Retryable; the worker decides."""


def job_context(job, *, score: float | None = None, description: str | None = None,
                fit_notes: str | None = None, salary_bands: dict | None = None) -> str:
    """What the model is told about the posting — the reference instance's context, ported whole."""
    # **The date the document is written.** Nothing supplied it, so the model wrote one from its
    # own sense of time: a cover letter generated in August 2026 went out dated 22 June 2026. It is
    # the first line an employer reads and a wrong one reads as carelessness, on a document nobody
    # on this platform proofreads before it is sent.
    lines = [f"Today's date: {datetime.now(UTC).strftime('%d %B %Y')}",
             f"Title: {job.title or ''}",
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
        # How contested it is.
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
    """What the posting pays and what this candidate should be asking for."""
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
    from libs.corpus.filters import age_label  # noqa: PLC0415 - one caller

    if posted_at is None:
        return ""
    return age_label(posted_at.isoformat() if hasattr(posted_at, "isoformat") else str(posted_at))



def fill_role_angles(template: str, role_angles: str | None) -> str:
    """Put this candidate's role angles into the prompt, or take the instruction out."""
    if "<<ROLE_ANGLES>>" not in template:
        return template                       # a prompt from before the split, or the user's own
    angles = (role_angles or "").strip()
    if not angles or any(marker in angles for marker in PLACEHOLDER_MARKERS):
        return template.replace("<<ROLE_ANGLES>>", "")
    return template.replace("<<ROLE_ANGLES>>", f"ROLE ANGLES FOR THIS CANDIDATE\n{angles}")


def build_prompt(profile: ProfileSchema, base_html: str, context: str, *,
                 template: str | None = None, feedback: str | None = None,
                 role_angles: str | None = None) -> str:
    """The SACRED prompt, filled: the candidate, the job, and the base document."""
    prompt = (fill_role_angles(render_cv_prompt(profile, template=template), role_angles)
              .replace("<JOB_CONTEXT>", context)
              .replace("<BASE_TEMPLATE>", base_html))
    if feedback:
        prompt += f"\n\nREVISION NEEDED: {feedback}\nReturn the full corrected HTML document."
    return prompt


def tailor(client, profile: ProfileSchema, base_html: str, context: str, *,
           facts_md: str | None = None, feedback: str | None = None, template: str | None = None,
           role_angles: str | None = None, model: str = CV_MODEL) -> str:
    """One call."""
    stripped, style = strip_style(base_html)
    prompt = build_prompt(profile, strip_comments(stripped), context, template=template,
                          role_angles=role_angles,
                          feedback=feedback)

    reply = client.complete_text(
        model=model,
        system=facts_md or _MINIMAL_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS,
        thinking_tokens=THINKING_TOKENS)

    html = extract_html(reply)
    if html is None:
        # Never log the reply: it is the candidate's CV.
        started = "<html" in reply.lower()
        logger.error("the tailor call returned %d chars with no complete HTML document "
                     "(document %s)", len(reply),
                     "started and was cut off" if started else "never started")
        raise BuildFailed("the model did not return a complete HTML document")

    return sanitise(restore_style(html, style))


# Used only when a user somehow has no rendered facts block.
_MINIMAL_SYSTEM = ("Write only what the base template below already states about the candidate. "
                   "Never invent an employer, a date, a metric, or a technology.")


# ── the cover letter ──────────────────────────────────────────────────────────────────────────

# Chars of plain-text CV sent to the cover prompt.
CV_TEXT_MAX = 1_500


def tailor_cover(client, profile: ProfileSchema, cv_html: str, context: str, *,
                 facts_md: str | None = None, template: str | None = None,
                 model: str = CV_MODEL) -> str:
    """The cover letter for one job, built from the CV that was just tailored for it."""
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

    # Em-dashes come out of the whole letter, not just its bullets.
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
    """The mechanical enforcement of the rules the prompt states and the model breaks."""
    return strip_hidden(strip_prose_emdashes(html))


def strip_hidden(html: str) -> str:
    """Zero-width characters out, and any inline `style` attribute that hides text."""
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
    """Remove the `<style>` block before sending, and hand it back for restoration."""
    match = _STYLE.search(html)
    if not match:
        return html, ""
    return html[:match.start()] + html[match.end():], match.group(0)


def strip_comments(html: str) -> str:
    """Drop the template's HTML comments before sending."""
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

    body = re.search(r"(?i)<body\b[^>]*>", tailored)
    if body:
        return tailored[:body.start()] + f"<head>\n{style}\n</head>\n" + tailored[body.start():]

    # No head, no body: whatever came back is the content.
    return ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
            f"{style}\n</head>\n<body>\n{tailored}\n</body>\n</html>")


def extract_html(reply: str) -> str | None:
    """The document out of the reply, ignoring any commentary around it."""
    if not reply:
        return None
    match = _HTML.search(reply)
    return match.group(1) if match else None


def strip_prose_emdashes(html: str) -> str:
    """Em-dashes out of bullets and the profile paragraph, left alone in entry titles."""
    def fix(match: re.Match[str]) -> str:
        return _EMDASH.sub(", ", match.group(0))

    html = re.sub(r"(?is)<li\b[^>]*>.*?</li>", fix, html)
    return re.sub(r'(?is)<p\b[^>]*class="[^"]*profile[^"]*"[^>]*>.*?</p>', fix, html)


def scan_hidden(html: str) -> list[str]:
    """Invisible content in the body."""
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
    """What to tell the model when the PDF came out too long, or nothing when it did not."""
    if pages is None or pages <= maximum:
        return None
    return (f"Your CV compiled to {pages} pages. It must not exceed {maximum} pages of A4, and one "
            f"sharp page is better than two. Cut the least-relevant entries and bullets until it "
            f"fits. Do not shrink the font, the padding or the margins, and do not pad what "
            f"remains.")


def template_base() -> str:
    """The unfilled template. Only for callers that need the layout without a profile."""
    return load_template(BASE_CV_TEMPLATE)


