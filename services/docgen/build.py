"""Writing one CV or cover letter for one job: what the model is given, and what is kept of its answer.

**One call a document** (owner, 2026-11-10). The prompt (`libs/prompts/write_cv.md`, `write_cover.md`)
carries the job and everything the person gave us: every CV they switched on, for a letter every cover
letter too, their "About you", their other files and their languages. Then what to do, then a final
check. Code decides everything it can (CLAUDE.md §0): the model writes the middle of the document in the
one markup every look lays out (`libs/templates`), and code writes the rest of it: the header (their name,
contact details and links), on a letter the company and its place, and the look they picked.

What comes back is cleaned to that markup and nothing else, so no style, script or hidden text can reach
an employer. Then it is checked (`problems`): too many pages or a short last one, a number found nowhere
in the person's documents or the posting, and what a reviewer would send back (`checks`) are said back once
(`feedback`) for a second answer.

The profile files the setup agents wrote (`base_cv.html`, `tailor_cv.md`, `tailor_cover.md`, …) are no
longer read: a person's own uploads replaced them (owner, 2026-11-10). They stay where they are, as those
people's work.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape, unescape
from html.parser import HTMLParser

from libs import prompts
from libs.documents.read import LANGUAGE_NAMES, read, texts
from libs.templates import Template
from services.docgen import checks

logger = logging.getLogger(__name__)

PROMPTS = {"cv": "write_cv.md", "cover": "write_cover.md"}
# What the model's answer is wrapped in: anything around it, its working or a word of its own, is dropped.
TAGS = {"cv": "cv", "cover": "letter"}
# The most pages of A4 each may be. More is said back once.
MAX_PAGES = {"cv": 2, "cover": 1}

# Room for a two-page CV after its thinking.
MAX_TOKENS = 16_000
# How hard the model thinks before it writes (`output_config.effort`; Claude Code's `--effort`), given on
# every call so an API key and a Claude sign-in write alike: unset, a key ran at Haiku 5.5's own default
# and a sign-in at Claude Code's, which nobody had chosen. Measured 2026-11-11 (CHANGELOG).
EFFORT = "medium"
# Less than this on the last of two pages is a few lines that spilled over (`tailor_cv.md`, failure 4).
SHORT_LAST_PAGE_CHARS = 500

# The posting, capped. Measured 2026-11-11 over the 27,275 described postings stored here: median 3,678
# characters, longest 13,985. The 6,000 the writer took before cut the end off 17.1% of them; this cuts none.
POSTING_MAX_CHARS = 15_000

SYSTEM = ("You write CVs and cover letters for people applying for jobs, using only what they have told "
          "you about themselves.")

# What the person gave us, by the kind it was uploaded as, under the name the prompt gives it.
OTHER_KINDS = {"linkedin": "LinkedIn profile", "reference": "Reference", "achievement": "Achievement",
               "diploma": "Diploma or certificate", "other": "Other document"}
# A prompt's places to fill: `{{job}}`.
_SLOT = re.compile(r"\{\{(\w+)\}\}")


class BuildFailed(RuntimeError):
    """The model did not return a usable document. Retryable; the worker decides."""


@dataclass(frozen=True)
class Request:
    """One document to write: the filled prompt, and what code puts around the answer."""

    kind: str
    prompt: str
    # Their name, and the header and title of the page, written by code.
    name: str
    header: str
    # On a letter, the company and its place.
    letter_to: str
    look: Template
    # Every text a number in the document may come from: their documents and the posting.
    sources: str
    # The employer, which a letter's opening names, and the posting, whose own words a letter may quote.
    company: str = ""
    posting: str = ""


def prepare(kind: str, *, user, job, documents: dict[str, list[str]], look: Template,
            languages: tuple[str, ...] = (), template: str | None = None,
            today: datetime | None = None) -> Request:
    """The request for `kind` ("cv" or "cover"): `documents` are their switched-on texts by kind
    (`libs.suggestions.documents_of`), `languages` the job languages their search accepts, and `template`
    a prompt to try in place of the live one (the CV bench's variants)."""
    facts = read(documents)
    name, header = _header(facts, {"full_name": user.full_name, "links": user.links or []})
    spoken = [f"From their CV: {', '.join(_names(facts.languages))}."] if facts.languages else []
    if languages:
        spoken.append(f"The jobs they are looking for are in: {', '.join(_names(languages))}.")
    values = {
        "job": _job(job),
        "cvs": _each(texts(documents, "cv"), "CV"),
        "letters": _each(texts(documents, "cover_letter"), "Cover letter"),
        "about": "\n\n".join(texts(documents, "history")) or "(nothing)",
        "other": "\n\n".join(_each(texts(documents, kind_), label, alone=False)
                             for kind_, label in OTHER_KINDS.items() if documents.get(kind_)) or "(none)",
        "languages": " ".join(spoken) or "Not stated.",
        "name": name or "(not given)",
        "today": (today or datetime.now(UTC)).strftime("%d %B %Y"),
    }
    text = _SLOT.sub(lambda slot: values[slot.group(1)], template or prompts.load(PROMPTS[kind]))
    company, place = (job.company or "").strip(), (job.location or "").strip()
    sources = "\n".join([*(text_ for kind_ in documents for text_ in texts(documents, kind_)),
                         job.title or "", company, place, job.description or ""])
    return Request(kind=kind, prompt=text, name=name, header=header, look=look, sources=sources,
                   letter_to="<br>".join(escape(each) for each in (company, place) if each), company=company,
                   posting=job.description or "")


def draft(client, request: Request, *, model: str, revision: tuple[str, str] | None = None,
          effort: str = EFFORT) -> tuple[str, str]:
    """One call: the model's whole reply, and the document in it, cleaned. `revision` is the reply
    before and what was wrong with it: the second call is that conversation, continued. `effort` is the CV
    bench's to vary."""
    messages = [{"role": "user", "content": request.prompt}]
    if revision is not None:
        messages += [{"role": "assistant", "content": revision[0]}, {"role": "user", "content": revision[1]}]
    reply = client.complete_text(model=model, system=SYSTEM, messages=messages, max_tokens=MAX_TOKENS,
                                 effort=effort)
    body = answer(reply, request.kind)
    if body is None:
        # Never log the reply: it is the person's CV.
        logger.error("docgen: the %s call returned %d chars with no complete <%s> in it", request.kind,
                     len(reply or ""), TAGS[request.kind])
        raise BuildFailed(f"the model did not return a complete {'CV' if request.kind == 'cv' else 'letter'}")
    return reply, body


def answer(reply: str | None, kind: str) -> str | None:
    """The document out of the reply, cleaned to the markup: the last complete `<cv>…</cv>`, as a model
    that thinks aloud may write the tag in its working first."""
    tag = TAGS[kind]
    found = re.findall(rf"(?is)<{tag}>(.*?)</{tag}>", reply or "")
    if not found or not found[-1].strip():
        return None
    body = clean(found[-1])
    if kind == "cover":
        return _undated(_EMDASH.sub(", ", body))
    return _one_profile(strip_prose_emdashes(body))


def assemble(request: Request, body: str) -> str:
    """The whole page: the look, the header, a letter's company and place, then what the model wrote."""
    meta = (f'<div class="letter-meta">\n<p class="letter-to">{request.letter_to}</p>\n</div>\n'
            if request.kind == "cover" and request.letter_to else "")
    title = f"{request.name}, {'CV' if request.kind == 'cv' else 'cover letter'}".strip(", ")
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n'
            f"<title>{escape(title)}</title>\n{request.look.style}\n</head>\n<body>\n"
            f"{request.header}\n{meta}{body}\n</body>\n</html>\n")


@dataclass(frozen=True)
class Check:
    """What is wrong with a document: its pages, its numbers, and what a reviewer would send back."""

    pages_over: int
    invented: tuple[str, ...]
    faults: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.pages_over and not self.invented and not self.faults

    def worse_than(self, other: Check) -> bool:
        def weight(check: Check) -> tuple[int, int, int]:
            return check.pages_over, len(check.invented), len(check.faults)
        return weight(self) > weight(other)


def problems(request: Request, body: str, pages: int | None, last_page_chars: int | None = None) -> Check:
    over = max(0, (pages or 0) - MAX_PAGES[request.kind])
    if request.kind == "cv":
        found = checks.cv(body, request.sources)
        if not over and (pages or 0) > 1 and last_page_chars is not None \
                and last_page_chars < SHORT_LAST_PAGE_CHARS:
            found.insert(0, f"Only a few lines reach page {pages}. Cut the least relevant lines so it "
                            f"ends on page {pages - 1}.")
    else:
        found = checks.letter(body, company=request.company, posting=request.posting)
    return Check(pages_over=over, invented=numbers_not_in(text_of(body), request.sources),
                 faults=tuple(found))


def feedback(request: Request, check: Check, pages: int | None) -> str:
    """What the second call is told: only what the checks found."""
    noun = "CV" if request.kind == "cv" else "letter"
    found = []
    if check.pages_over:
        found.append(f"It came to {pages} pages of A4, and it must fit on {MAX_PAGES[request.kind]}. Cut the "
                     "least relevant parts. Do not shrink anything, and do not drop what matters most.")
    if check.invented:
        found.append(f"These numbers are not in their documents or the posting: {', '.join(check.invented)}. "
                     "Take each one out, or use what their documents actually say.")
    found += check.faults
    tag = TAGS[request.kind]
    return (f"Your {noun} needs fixing before it can be used:\n" + "\n".join(f"- {each}" for each in found)
            + f"\n\nFix only that, keep everything else as it is, and answer with the whole {noun} again, "
              f"between <{tag}> and </{tag}>.")


# ── what the model is told ────────────────────────────────────────────────────────────────────

def _job(job) -> str:
    workplace = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "on-site": "On-site"}.get(
        (getattr(job, "workplace", None) or "").lower(), "")
    place = ", ".join(each for each in ((job.location or "").strip(), workplace) if each)
    language = getattr(job, "language", None)
    posting = (job.description or "").strip() or "(no description was captured for this posting)"
    if len(posting) > POSTING_MAX_CHARS:
        posting = posting[:POSTING_MAX_CHARS] + f"\n[cut at {POSTING_MAX_CHARS} characters]"
    lines = [f"Job title: {job.title or ''}", f"Company: {job.company or 'not given'}",
             f"Location: {place or 'not given'}"]
    if language:
        lines.append(f"The posting is written in: {LANGUAGE_NAMES.get(language, (language,))[0]}")
    return "\n".join([*lines, "", posting])


def _names(codes) -> list[str]:
    return [LANGUAGE_NAMES.get(code, (code,))[0] for code in codes]


def _each(found: list[str], label: str, *, alone: bool = True) -> str:
    """Each document under its own heading, numbered when there are several."""
    if not found:
        return "(none)"
    if len(found) == 1 and alone:
        return found[0].strip()
    return "\n\n".join(f"=== {label} {index} of {len(found)} ===\n{text.strip()}"
                       for index, text in enumerate(found, start=1))


def _header(facts, identity: dict) -> tuple[str, str]:
    """Their name and contact line: what they typed in "Your name and links", else what their CV says."""
    name = (identity.get("full_name") or facts.name or "").strip()
    links = [link.get("url") if isinstance(link, dict) else str(link)
             for link in identity.get("links") or []] or list(facts.links)
    contact = [escape(each) for each in (facts.location, facts.phone) if each]
    if facts.email:
        contact.append(f'<a href="mailto:{escape(facts.email)}">{escape(facts.email)}</a>')
    contact += [f'<a href="{escape(url if "://" in url else f"https://{url}")}">'
                f'{escape(re.sub(r"^(?:https?://)?(?:www[.])?|/$", "", url))}</a>' for url in links if url]
    spans = "".join(f"<span>{each}</span>" for each in contact)
    return name, (f'<div class="cv-header">\n<div class="cv-name">{escape(name)}</div>\n'
                  f'<div class="cv-contact">{spans}</div>\n</div>\n<hr class="header-rule">')


# ── what is kept of the answer ────────────────────────────────────────────────────────────────

# The markup every look lays out (libs/templates/README.md), and nothing else.
_TAGS = frozenset({"p", "div", "span", "ul", "li", "strong", "em", "b", "i", "a", "br"})
_CLASSES = frozenset({"profile", "section", "competencies", "section-title", "entry", "entry-head",
                      "entry-title", "entry-date", "entry-desc", "plain-list", "letter-subject", "letter",
                      "closing", "signature"})
# Gone with everything inside them.
_DROPPED = frozenset({"script", "style", "head", "title", "template", "noscript", "svg", "math", "iframe",
                      "object", "header", "footer", "nav"})
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u202a-\u202e]")


class _Clean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROPPED:
            self.skipping += 1
        if self.skipping or tag not in _TAGS:
            return
        if tag == "br":
            self.out.append("<br>")
            return
        kept = ""
        values = dict(attrs)
        classes = [name for name in (values.get("class") or "").split() if name in _CLASSES]
        if classes:
            kept += f' class="{" ".join(classes)}"'
        href = (values.get("href") or "").strip()
        if tag == "a" and re.match(r"(?i)(?:https?://|mailto:)", href):
            kept += f' href="{escape(href)}"'
        self.out.append(f"<{tag}{kept}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROPPED:
            self.skipping = max(0, self.skipping - 1)
            return
        if not self.skipping and tag in _TAGS and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.out.append(escape(_ZERO_WIDTH.sub("", data), quote=False))


def clean(fragment: str) -> str:
    """Only the markup's own tags and class names, links that are links, and the text: no style, script,
    comment or invisible character. Whatever hid text in a document cannot survive this."""
    parser = _Clean()
    parser.feed(fragment)
    parser.close()
    return "".join(parser.out).strip()


def text_of(html: str) -> str:
    return checks.text(html)


def headings(body: str) -> tuple[str, ...]:
    """The CV's section titles, in order: what the ATS check looks for in its PDF."""
    return tuple(" ".join(text_of(each).split()) for each in re.findall(
        r'(?is)<div class="section-title">(.*?)</div>', body))


_PROFILE_P = re.compile(r'(?is)\s*<p class="profile">(.*?)</p>')


def _one_profile(body: str) -> str:
    """One profile paragraph, as every look lays it out: written as several, they are joined."""
    parts = _PROFILE_P.findall(body)
    if len(parts) < 2:
        return body
    first = _PROFILE_P.search(body)
    joined = " ".join(part.strip() for part in parts)
    return f'{body[:first.start()]}\n<p class="profile">{joined}</p>{_PROFILE_P.sub("", body[first.end():])}'


# A number as a person writes it: 40, 1,200, 3.5, 2021, 10k. Compared without its thousands commas, and a
# "10k" as the 10,000 it is: a letter wrote "10,000 records" for a CV's "10k+" and was sent back for it
# (2026-11-11).
_NUMBER = re.compile(r"(\d+(?:[.,]\d+)*)(?:\s?([kKmM])(?![a-zA-Z]))?")


def numbers_not_in(text: str, sources: str) -> tuple[str, ...]:
    """The numbers in `text` that appear nowhere in `sources`, each once: the fabrication a reader cannot
    see and an interviewer will ask about."""
    def forms(digits: str, scale: str) -> set[str]:
        plain = digits.replace(",", "")
        if not scale:
            return {plain}
        try:
            value = float(plain) * (1_000 if scale in "kK" else 1_000_000)
        except ValueError:
            return {plain}
        return {plain, str(int(value)) if value.is_integer() else str(value)}

    known = set().union(*(forms(*each) for each in _NUMBER.findall(sources)))
    return tuple(dict.fromkeys(match.group(0) for match in _NUMBER.finditer(text)
                               if not forms(match.group(1), match.group(2) or "") & known))


_EMDASH = re.compile(r"\s*(?:—|&mdash;|&#8212;)\s*")


def strip_prose_emdashes(html: str) -> str:
    """Em-dashes out of bullets and the profile paragraph, left alone in entry titles."""
    def fix(match: re.Match[str]) -> str:
        return _EMDASH.sub(", ", match.group(0))

    html = re.sub(r"(?is)<li\b[^>]*>.*?</li>", fix, html)
    return re.sub(r'(?is)<p\b[^>]*class="[^"]*profile[^"]*"[^>]*>.*?</p>', fix, html)


# A line that is a date and nothing else, the way a letter is dated: "8 November 2026", "November 8,
# 2026", "8. November 2026", "8 de novembro de 2026", "08/11/2026", "2026-11-08", after a place or a
# weekday ("Lisbon, 8 November 2026"). A date inside a sentence is never a line of its own.
_MONTH = r"[^\W\d_]{3,12}\.?"
_DATE_LINE = re.compile(
    r"(?:[^\W\d_]+(?:[ '-][^\W\d_]+)*,\s*|[^\W\d_]{3,12}\s+)?"
    rf"(?:\d{{1,2}}(?:st|nd|rd|th|\.)?\s+(?:de\s+)?{_MONTH}\s+(?:de\s+)?\d{{4}}"
    rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})")
# A paragraph or a box with no other paragraph or box inside it.
_INNERMOST = re.compile(r"(?is)<(p|div)\b[^>]*>((?:(?!<(?:p|div)\b).)*?)</\1>")
_BR_TAG = re.compile(r"(?i)<br\s*/?>")


def _undated(letter: str) -> str:
    """The letter without the day it was made (owner, 2026-11-08): each line that is only a date goes, and
    a paragraph left with nothing in it goes with it. The rest of a paragraph stays."""
    def line_text(part: str) -> str:
        return " ".join(unescape(re.sub(r"<[^>]+>", " ", part)).split())

    def without(match: re.Match) -> str:
        lines = _BR_TAG.split(match.group(2))
        kept = [part for part in lines if not _DATE_LINE.fullmatch(line_text(part))]
        if len(kept) == len(lines):
            return match.group(0)
        if not any(line_text(part) for part in kept):
            return ""
        opening = match.group(0)[:match.start(2) - match.start(0)]
        return f"{opening}{'<br>'.join(kept)}</{match.group(1)}>"

    return _INNERMOST.sub(without, letter)
