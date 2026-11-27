"""Writing one CV or cover letter for one job: what the model is given, and what is kept of its answer.

**One call a document** (owner, 2026-11-10). The prompt (`libs/prompts/write_cv.md`, `write_cover.md`)
carries the job and everything the person gave us: every CV they switched on, for a letter every cover
letter too, their "About you", their other files and their languages. Then what to do, then a final
check. Code decides everything it can (CLAUDE.md §0): the model writes the middle of the document in the
one markup every look lays out (`libs/templates`), and code writes the rest of it: the header (their name,
contact details and links) and the look they picked. A letter begins at its greeting (owner, 2026-11-26):
no block for the company and its place above it, without which it "would look smoother", and no subject
line, the job named in bold in the letter's own words instead.

What comes back is cleaned to that markup and nothing else, so no style, script or hidden text can reach
an employer. Then it is checked (`problems`): too many pages, a number found nowhere in the person's
documents or the posting, and what a reviewer would send back (`checks`) are said back once (`feedback`)
for a second answer. A few lines alone on a last page are not: the look is drawn slightly smaller to take
them up (`pdf.fitted`).

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
from libs.llm import cli
from libs.templates import Template
from services.docgen import checks

logger = logging.getLogger(__name__)

PROMPTS = {"cv": "write_cv.md", "cover": "write_cover.md"}
# What the model's answer is wrapped in: anything around it, its working or a word of its own, is dropped.
TAGS = {"cv": "cv", "cover": "letter"}
# What a whole document has, in the markup: a CV a section, a letter its body. A line of one quoted
# between the tags is not the document.
WHOLE = {"cv": 'class="section', "cover": 'class="letter"'}
# The most pages of A4 each may be. More is said back once.
MAX_PAGES = {"cv": 2, "cover": 1}

# Room for a two-page CV after its thinking, which is spent inside this on an API key (a sign-in's tool
# sets its own). 16,000 was too little: the proposed CV wording's largest of 18 took 15,457, and 11 of
# the 164 CVs the bench wrote with other wordings went over, to 22,275 (2026-11-15); the same ceiling
# once ended builds with nothing written (b7845ed). Billed by what is produced, not by what is allowed.
# At the slowest rate measured on claude-haiku-5-5, 239 tokens a second, this is 134 s of a call's 240
# (`libs.llm.client.TIMEOUT_SEC`), and it is inside each provider's own limit (Haiku 5.5 and GPT-6 Luna
# 128,000, Gemini 3.8 Flash 65,536).
MAX_TOKENS = 32_000
# How hard the model thinks before it writes (`output_config.effort`; Claude Code's `--effort`), given on
# every call so an API key and a Claude sign-in write alike: unset, a key ran at Haiku 5.5's own default
# and a sign-in at Claude Code's, which nobody had chosen. Measured 2026-11-11 (CHANGELOG).
EFFORT = "medium"
# The documents a ChatGPT sign-in writes lean, from this platform's instructions alone and without Codex's own
# and its tools (`libs.llm.cli.CliClient.lean`). A letter: 8,824 tokens in against 19,890, about half the
# person's plan, with both reviewers, the ATS checks and a blind language read level or better (owner,
# 2026-11-19, CHANGELOG). A CV is written plain until it is measured lean.
LEAN = frozenset({"cover"})

# Whether a name found in neither their documents nor the posting is said back for a second call
# (`names_not_in`). Off: the owner, 2026-11-15, "keep the name check just in case but remove it from
# play". Every name it found in 208 written CVs was an abbreviation, which is allowed, and with those
# let through it found nothing; it stays here, tested, for the day a made-up "QuickBooks" is seen.
NAMES_SAID_BACK = False

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


# What the writer is told of a company the platform knows no more about than its posting says.
NOTHING_MORE = "Nothing more is known about the company than the posting says."


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
    look: Template
    # Every text a number or a name in the document may come from: their documents and the posting.
    sources: str
    # Their documents alone: the language they are in.
    theirs: str = ""
    # The employer, which a letter's opening names, and the posting, whose own words a letter may quote.
    company: str = ""
    posting: str = ""


def prepare(kind: str, *, user, job, documents: dict[str, list[str]], look: Template,
            languages: tuple[str, ...] = (), template: str | None = None,
            today: datetime | None = None, company: str = "") -> Request:
    """The request for `kind` ("cv" or "cover"): `documents` are their switched-on texts by kind
    (`libs.suggestions.documents_of`), `languages` the job languages their search accepts, and `template`
    a prompt to try in place of the live one (the CV bench's variants). `company` is what the employer says
    of itself beyond this posting (`libs.corpus.companies.about`), for a letter's reason for wanting it."""
    facts = read(documents)
    name, header = _header(facts, {"full_name": user.full_name, "links": user.links or []})
    spoken = [f"From their CV: {', '.join(_names(facts.languages))}."] if facts.languages else []
    if languages:
        spoken.append(f"The jobs they are looking for are in: {', '.join(_names(languages))}.")
    values = {
        "job": _job(job),
        "company": company.strip() or NOTHING_MORE,
        "cvs": _each(texts(documents, "cv"), "CV"),
        "letters": _each(texts(documents, "cover_letter"), "Cover letter"),
        "about": "\n\n".join(texts(documents, "history")) or "(nothing)",
        "other": "\n\n".join(_each(texts(documents, kind_), label, alone=False)
                             for kind_, label in OTHER_KINDS.items() if documents.get(kind_)) or "(none)",
        "languages": " ".join(spoken) or "Not stated.",
        "name": name or "(not given)",
        "today": (today or datetime.now(UTC)).strftime("%d %B %Y"),
        # What a page of the look they picked holds (`templates.PAGE_WORDS`).
        "page_words": str(look.page_words or ""),
    }
    text = _SLOT.sub(lambda slot: values[slot.group(1)], template or prompts.load(PROMPTS[kind]))
    company, place = unescape(job.company or "").strip(), unescape(job.location or "").strip()
    theirs = "\n".join(text_ for kind_ in documents for text_ in texts(documents, kind_))
    sources = "\n".join([theirs, job.title or "", company, place, job.description or ""])
    return Request(kind=kind, prompt=text, name=name, header=header, look=look, sources=sources,
                   theirs=theirs, company=company, posting=job.description or "")


def draft(client, request: Request, *, model: str, revision: tuple[str, str] | None = None,
          effort: str = EFFORT) -> tuple[str, str]:
    """One call: the model's whole reply, and the document in it, cleaned. `revision` is the reply
    before and what was wrong with it: the second call is that conversation, continued. `effort` is the CV
    bench's to vary."""
    messages = [{"role": "user", "content": request.prompt}]
    if revision is not None:
        messages += [{"role": "assistant", "content": revision[0]}, {"role": "user", "content": revision[1]}]
    # Told with the call and never set on the client, which other calls may be using at the same moment.
    lean = {"lean": True} if request.kind in LEAN and isinstance(client, cli.CliClient) else {}
    reply = client.complete_text(model=model, system=SYSTEM, messages=messages, max_tokens=MAX_TOKENS,
                                 effort=effort, **lean)
    body = answer(reply, request.kind)
    if body is None:
        # Never log the reply: it is the person's CV.
        logger.error("docgen: the %s call returned %d chars with no complete <%s> in it", request.kind,
                     len(reply or ""), TAGS[request.kind])
        raise BuildFailed(f"the model did not return a complete {'CV' if request.kind == 'cv' else 'letter'}")
    return reply, body


def answer(reply: str | None, kind: str) -> str | None:
    """The document out of the reply, cleaned to the markup: the last complete `<cv>…</cv>` that is a
    whole document (`WHOLE`). A model that thinks aloud may write the tag in its working first, or quote a
    line of its answer between the tags after it: one CV in 44 written for the panel came out as that one
    quoted sentence, and would have been somebody's PDF (2026-11-14)."""
    tag = TAGS[kind]
    found = [body for body in map(clean, re.findall(rf"(?is)<{tag}>(.*?)</{tag}>", reply or ""))
             if WHOLE[kind] in body]
    if not found:
        return None
    body = found[-1]
    if kind == "cover":
        # A letter is its greeting to its signature and nothing above them: a subject line or an address a
        # model still writes from habit is left out here, whatever the prompt asked (CLAUDE.md §0).
        body = body[body.index(_LETTER_OPENS):] if _LETTER_OPENS in body else body
        return _undated(checks.paragraphs_split(_EMDASH.sub(", ", body)))
    return _one_profile(strip_prose_emdashes(body))


def assemble(request: Request, body: str) -> str:
    """The whole page: the look, the header, then what the model wrote."""
    title = f"{request.name}, {'CV' if request.kind == 'cv' else 'cover letter'}".strip(", ")
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n'
            f"<title>{escape(title)}</title>\n{request.look.style}\n</head>\n<body>\n"
            f"{request.header}\n{body}\n</body>\n</html>\n")


@dataclass(frozen=True)
class Check:
    """What is wrong with a document: its pages, the numbers and names found nowhere in their documents
    or the posting, and what a reviewer would send back."""

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


def problems(request: Request, body: str, pages: int | None, spilled: bool = False) -> Check:
    """`spilled`: only a few lines reach the last page and no smaller drawing of the look took them up
    (`pdf.PdfReport.spilled`)."""
    over = max(0, (pages or 0) - MAX_PAGES[request.kind])
    if request.kind == "cv":
        found = checks.cv(body, request.sources)
        if not over and spilled:
            found.insert(0, f"Only a few lines reach page {pages}. Cut the least relevant lines so it "
                            f"ends on page {pages - 1}.")
    else:
        found = checks.letter(body, company=request.company, posting=request.posting)
    written = text_of(body)
    invented = numbers_not_in(written, request.sources)
    # A name is checked in a CV written in the language of their documents: in another, a faithful
    # translation ("VAT" for "áfa") is in neither their documents nor the posting.
    if (NAMES_SAID_BACK and request.kind == "cv"
            and checks.language(written) == checks.language(request.theirs)):
        invented += names_not_in(written, request.sources)
    return Check(pages_over=over, invented=invented, faults=tuple(found))


def feedback(request: Request, check: Check, pages: int | None) -> str:
    """What the second call is told: only what the checks found."""
    noun = "CV" if request.kind == "cv" else "letter"
    found = []
    if check.pages_over:
        found.append(f"It came to {pages} pages of A4, and it must fit on {MAX_PAGES[request.kind]}. Cut the "
                     "least relevant parts. Do not shrink anything, and do not drop what matters most.")
    if check.invented:
        found.append(f"These are in neither their documents nor the posting: {', '.join(check.invented)}. "
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
    place = ", ".join(each for each in (unescape(job.location or "").strip(), workplace) if each)
    language = getattr(job, "language", None)
    # As a person reads them: a board's "R&amp;D" is "R&D", in the prompt and so in the document.
    posting = unescape(job.description or "").strip() or "(no description was captured for this posting)"
    if len(posting) > POSTING_MAX_CHARS:
        posting = posting[:POSTING_MAX_CHARS] + f"\n[cut at {POSTING_MAX_CHARS} characters]"
    lines = [f"Job title: {unescape(job.title or '')}",
             f"Company: {unescape(job.company or '') or 'not given'}",
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
    # The phone and the email before the place: with the place first, Affinda's resume parser read no
    # phone number in 4 of 4 CVs ("Greater Manchester 07700 900236"), and all three with it last
    # (2026-11-14).
    contact = [escape(facts.phone)] if facts.phone else []
    if facts.email:
        contact.append(f'<a href="mailto:{escape(facts.email)}">{escape(facts.email)}</a>')
    if facts.location:
        contact.append(escape(facts.location))
    contact += [f'<a href="{escape(url if "://" in url else f"https://{url}")}">'
                f'{escape(re.sub(r"^(?:https?://)?(?:www[.])?|/$", "", url))}</a>' for url in links if url]
    spans = "".join(f"<span>{each}</span>" for each in contact)
    return name, (f'<div class="cv-header">\n<div class="cv-name">{escape(name)}</div>\n'
                  f'<div class="cv-contact">{spans}</div>\n</div>\n<hr class="header-rule">')


# ── what is kept of the answer ────────────────────────────────────────────────────────────────

# The markup every look lays out (libs/templates/README.md), and nothing else.
_TAGS = frozenset({"p", "div", "span", "ul", "li", "strong", "em", "b", "i", "a", "br"})
_CLASSES = frozenset({"profile", "section", "competencies", "section-title", "entry", "entry-head",
                      "entry-title", "entry-date", "entry-desc", "plain-list", "letter", "closing",
                      "signature"})
_LETTER_OPENS = '<div class="letter">'
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
            # One space wherever the model left white space: a line break in its markup is not one on the
            # page, and every look keeps the white space it is given (`templates.EVERY_LOOK`).
            # Decoded once more: a model that writes "&amp;amp;" means "&", and the page would show
            # "&amp;" (owner, 2026-11-15: that must not occur).
            self.out.append(escape(unescape(_SPACES.sub(" ", _ZERO_WIDTH.sub("", data))), quote=False))


_SPACES = re.compile(r"\s+")
# The white space the markup is laid out with, either side of a paragraph's, a box's or a list's tag: none
# of it is text.
_AROUND_A_BLOCK = re.compile(r" ?(</?(?:p|div|ul|li|br)\b[^>]*>) ?")


def clean(fragment: str) -> str:
    """Only the markup's own tags and class names, links that are links, and the text, with one space
    between words: no style, script, comment or invisible character. Whatever hid text in a document
    cannot survive this."""
    parser = _Clean()
    parser.feed(fragment)
    parser.close()
    return _AROUND_A_BLOCK.sub(r"\1", "".join(parser.out).strip())


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
# Thousands set apart by a space, as Polish, French or Hungarian write them: "1 100" is one number, the
# "1,100" an English letter makes of a Polish CV's "ok. 1 100 osób" (2026-11-19). Only from a group of one
# to three figures: "2019 120 clients" is a year and a count.
_SPACED = re.compile(r"(?<!\d)(\d{1,3})((?:[ \u00a0\u202f]\d{3})+)(?!\d)")


def numbers_not_in(text: str, sources: str) -> tuple[str, ...]:
    """The numbers in `text` that appear nowhere in `sources`, each once: the fabrication a reader cannot
    see and an interviewer will ask about. A number is theirs however its language writes it: "1 100",
    "1.100" and "1,100" are one number, and so are "3,5" and "3.5"."""
    def forms(digits: str, scale: str) -> set[str]:
        plain = digits.replace(",", "")
        found = {plain}
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", digits):
            found.add(digits.replace(".", ""))
        if re.fullmatch(r"\d+,\d{1,2}", digits):
            found.add(digits.replace(",", "."))
        if not scale:
            return found
        try:
            value = float(plain) * (1_000 if scale in "kK" else 1_000_000)
        except ValueError:
            return found
        return found | {str(int(value)) if value.is_integer() else str(value)}

    def joined(part: str) -> str:
        return _SPACED.sub(lambda found: found.group(1) + re.sub(r"\D", "", found.group(2)), part)

    written = _NUMBER.findall(sources) + _NUMBER.findall(joined(sources))
    known = set().union(*(forms(*each) for each in written))
    # A date written with dots is read as one number ("07.02.2029", "2028.05.16."): its year is theirs too.
    known |= {part for digits, _ in _NUMBER.findall(sources) if digits.count(".") >= 2
              for part in digits.split(".") if len(part) == 4}
    return tuple(dict.fromkeys(match.group(0) for match in _NUMBER.finditer(joined(text))
                               if not forms(match.group(1), match.group(2) or "") & known))


# A name as a tool, a method or a qualification is written: three characters or more, with a capital
# after its first letter or a figure after it ("DevOps", "QuickBooks", "GA4"). A word that only starts
# with a capital is any word at the start of a line, and in German any noun. Capitals alone, or with a
# plural's "s", are an abbreviation, and abbreviations are allowed (owner, 2026-11-15).
_NAME_PARTS = re.compile(r"[\s,;:()\[\]/&·\u2013\u2014-]+")


def names_not_in(text: str, sources: str) -> tuple[str, ...]:
    """The names in `text` that appear nowhere in `sources`, each once: a tool or a method the model
    brought in itself, as it can a number.

    An abbreviation is not one of them (owner, 2026-11-15: "abbreviations are allowed"). Every name this
    found in 208 written CVs was one: "AGM" for their "annual general meeting", "KPIs", "CRM" over
    HubSpot, and once "ROAS" in "ROAS bidding", which that marketer had never done but whose letter does
    say "return on ad spend". Each cost a second call, 12 of 108 panel CVs, and no reviewer counted one
    untrue. What is left is a name with a small letter or a figure in it, which nothing has yet been
    caught by."""
    # As they wrote it, and without its full stops: a "B.Sc." is a "BSc".
    known = sources.casefold()
    known = f"{known}\n{known.replace('.', '')}"

    def said(name: str) -> bool:
        forms = {name, name[:-1]} if name.endswith("s") and len(name) > 3 else {name}
        return any(re.search(rf"(?<![^\W_]){re.escape(form.casefold())}s?(?![^\W_])", known)
                   for form in forms)

    names = (part.strip(".'\"\u201c\u201d\u2018\u2019!?") for part in _NAME_PARTS.split(text))
    return tuple(dict.fromkeys(
        name for name in names
        if len(name) >= 3 and name[0].isalpha() and name.isalnum()
        and any(ch.isupper() or ch.isdigit() for ch in name[1:])
        and not _abbreviation(name) and not said(name)))


def _abbreviation(name: str) -> bool:
    letters = name[:-1] if name.endswith("s") else name
    return letters.isalpha() and letters.isupper()


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
