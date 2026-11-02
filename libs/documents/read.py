"""Reading a person's documents without AI: who they are, what they have done, and their CV's shape.

Rules, not a model (CLAUDE.md §0). Every fact here is a line of their own text, found by its place and
its shape, so a wrong answer can be traced to the line that caused it. Written against text as
`extract.py` leaves it: python-docx drops Word's bullet glyphs, pypdf flattens a two-column layout and
puts the sidebar first, a long bullet wraps onto a second line.

The one reader. The suggestions, the onboarding review and the instant CV all start from `read`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from libs.corpus.geo import CITIES, CITY_COUNTRY, COUNTRIES, place_names, read_country

# The names a language goes by, keyed by the platform's `Language` codes. The first is its label.
LANGUAGE_NAMES: dict[str, tuple[str, ...]] = {
    "en": ("English", "Englisch", "Anglais", "Inglés", "Angol"),
    "de": ("German", "Deutsch", "Allemand", "Alemán", "Német"),
    "fr": ("French", "Français", "Französisch", "Francés", "Francia"),
    "es": ("Spanish", "Español", "Castellano", "Spanisch", "Espagnol", "Spanyol"),
    "nl": ("Dutch", "Nederlands", "Niederländisch"),
    "hu": ("Hungarian", "Magyar", "Ungarisch"),
    "pl": ("Polish", "Polski", "Polnisch"),
    "pt": ("Portuguese", "Português", "Portugiesisch"),
    "it": ("Italian", "Italiano", "Italienisch", "Olasz"),
    "sv": ("Swedish", "Svenska", "Schwedisch"),
    "da": ("Danish", "Dansk", "Dänisch"),
}

# A heading is a whole line saying one of these. Order matters: the first kind that matches wins.
_HEADINGS = (
    ("other", r"volunteer(?:ing)? experience|(?:\w+ )?(?:certifications?|certificates|licen[cs]es"
              r"(?: (?:and|&) certifications)?|courses|projects|publications|awards|honou?rs|"
              r"achievements|volunteering|interests|hobbies(?: (?:and|&) interests)?|references|"
              r"memberships|activities)|additional information"),
    ("languages", r"languages?(?: skills)?"),
    ("experience", r"(?:\w+ ){0,2}experience|employment(?: history)?|work history|career history|"
                   r"career summary|professional background|positions held"),
    ("education", r"education(?: (?:and|&) (?:training|qualifications))?|(?:academic )?qualifications|"
                  r"academic background|training|studies"),
    ("skills", r"(?:\w+ ){0,2}(?:skills|competencies|competences)|areas of expertise|expertise|"
               r"technologies|tech stack|tools|strengths"),
    ("profile", r"(?:professional |personal |career )?(?:profile|summary|objective)|about(?: me)?|"
                r"personal statement|summary of qualifications|overview"),
    ("contact", r"contacts?(?: details| information)?|personal (?:details|information)"),
)
_HEADING = [(kind, re.compile(rf"(?:{pattern})")) for kind, pattern in _HEADINGS]

# The dashes CVs write: hyphen, en dash and em dash, between dates and parts and as bullets.
_DASH = "-–—"  # noqa: RUF001
# A bullet glyph at the start of a line, Word's Symbol-font ones (U+F0xx) included. A dash only
# when a space follows, so "-5%" is not a bullet.
_BULLET = re.compile(rf"^(?:[•●▪◦○■►▸‣⁃∙·*✓✔➢➤\uf0a7\uf0b7\uf076\uf0d8\uf0fc]|[{_DASH}](?=\s))\s*")  # noqa: RUF001

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_DATE = (r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}"
         r"|\d{1,2}\s*[/.]\s*\d{4}|\d{4})")
_RANGE = re.compile(rf"(?i)(?<![\w/.])({_DATE})\s*(?:[{_DASH}]|to|until|till)\s*"
                    rf"({_DATE}|present|current|now|today|date|ongoing)(?!\w)")
# A single date that dates an education entry: at the very start or the very end of its line.
_SINGLE = re.compile(rf"(?i)(?:^\(?({_DATE})\)?(?=\W|$)|(?<![\w/.])\(?({_DATE})\)?$)")
# "(4 years 10 months)", as LinkedIn writes beside a date range.
_DURATION = re.compile(r"(?i)\(\s*(?:\d+\s*(?:years?|yrs?|months?|mos?)\s*)+\)")
_STATED_YEARS = re.compile(r"(?i)\b(\d{1,2})\s*\+?\s*(?:years?|yrs)\b")

# An email address. Public so the one other pattern for it, in `services/docgen/pdf.py`, can import it.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w+])(?:\+|00|\()?\d[\d ()./-]{7,}\d(?!\w)")
_PHONE_LABEL = re.compile(r"(?i)\b(?:phone|tel|mobile|mob|cell|telefon)\b")
_LINK = re.compile(r"(?i)(?<![@\w.])(?:https?://|www\.|(?:[\w-]+\.)?(?:linkedin|github|gitlab|behance|"
                   r"dribbble|stackoverflow|medium|kaggle|bitbucket|xing)\.(?:com|net|io|de)/)\S+")
_LOW_LEVEL = re.compile(r"(?i)\b(?:a1|a2|basic|beginner|elementary|limited)\b")

# Splits an entry's header into its parts: title, organisation, place.
_PARTS = re.compile(rf"\s+[{_DASH}|·•]\s+|\s*\|\s*|,\s+|\s+(?:at|@)\s+|\t+")

# Words that make a phrase a job title. What tells "Staff Nurse" from "St. James's Hospital".
_TITLE_WORDS = frozenset("""
    engineer developer programmer architect analyst scientist designer manager director head lead
    chief officer executive representative associate assistant consultant specialist coordinator
    administrator advisor adviser technician nurse midwife doctor physician surgeon therapist
    pharmacist paramedic carer caregiver teacher lecturer tutor professor instructor trainer
    accountant auditor bookkeeper clerk cashier receptionist secretary driver chef cook waiter
    waitress barista bartender cleaner operator worker labourer electrician plumber mechanic welder
    intern trainee apprentice volunteer owner founder partner president ceo cto cfo coo vp agent broker
    buyer planner recruiter editor writer journalist translator interpreter researcher sdr bdr
    supervisor attendant guard porter librarian photographer artist marketer strategist producer
    controller tester devops sre fellow housekeeper pilot dentist hygienist veterinarian salesperson
    """.split())  # noqa: SIM905 - a word list reads best as words
# And what makes a phrase the title of an education entry.
_DEGREE = re.compile(r"(?i)\b(?:bachelor|master|b\.?sc|m\.?sc|b\.?a|m\.?a|mba|ph\.?d|doctorate|diploma|"
                     r"degree|certificate|b\.?eng|m\.?eng|llb|llm|hnd|pgce|a-levels?|gcses?)\b")

# Lowercase words a title-cased line may still contain: "Head of Sales", "Ana de la Cruz".
_SMALL = frozenset("of and the for at in on to a an & de la le van von der du da di y e und".split())  # noqa: SIM905

# Function words, which say nothing about whether a bullet and a posting are about the same thing.
_STOPWORDS = frozenset("""
    a an and the of to in for on with at by from as is are was were be been being our we you your will
    this that it its or not have has had can all who what their they them about into over more most
    also other such per any each than then there these those which while within without across up out
    new using use used via etc i me my he she his her us
    """.split())  # noqa: SIM905 - a word list reads best as words


def fold(text: str) -> str:
    """Lowercased, accents off: "Zürich" and "zurich" are one place."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


# Every way the countries the config offers are written (`libs.corpus.geo`), folded as a line is, and
# every city to the country it is in: a CV that says "Budapest" offers Hungary.
_PLACE_NAMES = {fold(name): CITY_COUNTRY.get(slug, slug) for slug in (*CITIES, *COUNTRIES)
                for name in (slug.replace("_", " "), *place_names(slug))}
# Longest first, so "northern ireland" is read before "ireland" can be.
_PLACES = re.compile(r"(?<!\w)(" + "|".join(sorted(map(re.escape, _PLACE_NAMES), key=len, reverse=True))
                     + r")(?!\w)")


def stem(word: str) -> str:
    """A word without its common ending, so "nursing", "nurses" and "nurse" compare equal."""
    for ending in ("ments", "ment", "ings", "ing", "ions", "ion", "ers", "er", "ies", "es", "ed",
                   "s", "e"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    return word


def words(text: str) -> set[str]:
    """The stems of every word in a text that carries meaning."""
    return {stem(word) for word in re.findall(r"[^\W_][\w+#]*", fold(text))
            if word not in _STOPWORDS and not word.isdigit()}


@dataclass(frozen=True)
class Entry:
    """One job or one qualification, as the CV states it."""

    title: str
    organisation: str
    # The header as written, title first, without the dates: what a CV built from this one shows.
    heading: str
    dates: str
    # Months since year 0, for ordering and for counting experience. `end` is None for "present".
    start: int | None
    end: int | None
    bullets: tuple[str, ...]


@dataclass(frozen=True)
class Section:
    heading: str
    # experience, education, skills, languages, profile, contact or other.
    kind: str
    lines: tuple[str, ...]
    entries: tuple[Entry, ...] = ()


@dataclass(frozen=True)
class CV:
    # Who they are: their name, a headline, the contact line. Usually the lines before the first
    # heading; for a sidebar CV, the name and the lines after it (`read_cv`).
    header: tuple[str, ...]
    sections: tuple[Section, ...]

    def of(self, kind: str) -> list[Section]:
        return [section for section in self.sections if section.kind == kind]


@dataclass(frozen=True)
class Facts:
    name: str | None
    email: str | None
    phone: str | None
    links: tuple[str, ...]
    # Where they are, as they wrote it ("Dublin, Ireland"), and the config places it names.
    location: str | None
    places: tuple[str, ...]
    languages: tuple[str, ...]
    # Experience entries, most recent first.
    jobs: tuple[Entry, ...]
    skills: tuple[str, ...]
    years: int | None
    cv: CV

    @property
    def titles(self) -> tuple[str, ...]:
        """Every job title held, most recent first, each once."""
        seen: dict[str, str] = {}
        for job in self.jobs:
            seen.setdefault(fold(job.title), job.title)
        return tuple(title for title in seen.values() if title)


# ── the structure ────────────────────────────────────────────────────────────────────────────

def heading_kind(line: str) -> str | None:
    """The kind of section a line opens, if it is a heading. "Langues / Languages" counts."""
    text = re.sub(r"\s+", " ", re.sub(r"^[\W_]+|[\W_]+$", "", line)).casefold().replace(" & ", " and ")
    if not text or len(text) > 40 or any(char.isdigit() for char in text):
        return None
    return next((kind for part in text.split(" / ") for kind, pattern in _HEADING
                 if pattern.fullmatch(part.strip())), None)


def _unspace(line: str) -> str:
    """A designer's letter-spaced heading, "W O R K   E X P E R I E N C E", as the words it spells."""
    chunks = re.split(r"\s{2,}", line)
    if all(re.fullmatch(r"(?:\w ){2,}\w", chunk) for chunk in chunks):
        return " ".join(chunk.replace(" ", "") for chunk in chunks)
    return line


def _lines(text: str) -> list[tuple[str, bool]]:
    """Each line once, its bullet glyph off, and a wrapped line joined back to the one it continues."""
    out: list[tuple[str, bool]] = []
    for raw in text.splitlines():
        line = _unspace(raw.strip())
        glyph = _BULLET.match(line)
        if glyph:
            line = line[glyph.end():].strip()
        if not line or re.fullmatch(r"(?i)page \d+ of \d+", line):
            continue
        # A wrapped line starts with a lowercase word; an email address or a link does not count.
        if (not glyph and out and re.match(r"[a-z][a-z'’-]*(?:[\s,;:)]|$)", line)  # noqa: RUF001
                and "@" not in line and not _LINK.search(line) and not heading_kind(out[-1][0])):
            out[-1] = (f"{out[-1][0]} {line}", out[-1][1])
            continue
        out.append((line, bool(glyph)))
    return out


def read_cv(text: str) -> CV:
    """The CV as header and sections, with the entries of its experience and education.

    The header is wherever their name is. pypdf reads a sidebar before the main column, so a
    two-column CV starts with "CONTACT" and its skills, and the name and headline arrive inside the
    sidebar's last section. They are moved back to the header when the email address agrees that it
    is their name — and never out of experience or education, where a wrong move would cost entries.
    """
    header: list[tuple[str, bool]] = []
    sections: list[tuple[str, str, list[tuple[str, bool]]]] = []
    for line, bullet in _lines(text):
        kind = None if bullet else heading_kind(line)
        if kind:
            sections.append((line, kind, []))
        else:
            (sections[-1][2] if sections else header).append((line, bullet))

    local = fold((_email([line for line, _ in header], [
        (kind, [line for line, _ in body]) for _, kind, body in sections]) or "@").split("@")[0])
    if not any(_name_like(line) for line, _ in header):
        for _, kind, body in sections:
            at = next((index for index, (line, _) in enumerate(body)
                       if kind not in ("experience", "education") and _name_like(line)
                       and _agrees(line, local)), None)
            if at is not None:
                header[:0] = body[at:]
                del body[at:]
                break
    return CV(header=tuple(line for line, _ in header), sections=tuple(
        Section(heading, kind, tuple(line for line, _ in body),
                _entries(body, single=kind == "education") if kind in ("experience", "education")
                else ())
        for heading, kind, body in sections))


def _month(text: str, *, end: bool = False) -> int | None:
    """A date as months since year 0; `None` for "present"."""
    lowered = text.casefold()
    if re.fullmatch(r"present|current|now|today|date|ongoing", lowered):
        return None
    year = int(re.findall(r"\d{4}", lowered)[-1])
    month = next((index for index, name in enumerate(_MONTHS) if lowered.startswith(name)), None)
    if month is None and (numeric := re.match(r"(\d{1,2})\s*[/.]", lowered)):
        month = int(numeric.group(1)) - 1
    if month is None or not 0 <= month < 12:
        month = 11 if end else 0
    return year * 12 + month


def _dates(line: str, *, single: bool) -> tuple[str, int | None, int | None] | None:
    """The dates a line carries, as written and as months; `None` when it carries none."""
    found = _RANGE.search(line)
    if found:
        start, end = _month(found.group(1)), _month(found.group(2), end=True)
    elif single and (found := _SINGLE.search(line.strip())):
        start = end = _month(found.group(1) or found.group(2), end=True)
    else:
        return None
    if start is None or not 1950 * 12 <= start <= (date.today().year + 1) * 12:
        return None
    return found.group(0).strip(" ()"), start, end


def _without_dates(line: str) -> str:
    line = _DURATION.sub("", _SINGLE.sub("", _RANGE.sub("", line.strip())))
    return re.sub(r"\(\s*\)", "", line).strip(f" |·•,{_DASH}\t")


def _has_title_word(text: str) -> bool:
    return any(word in _TITLE_WORDS for word in re.findall(r"[a-z]+", text.casefold()))


def _header_like(line: str, bullet: bool) -> bool:
    """A short line with every word capitalised: a title, an employer, a place — not a sentence."""
    if bullet or line.endswith(".") or len(line.split()) > 12:
        return False
    big = [word for word in re.findall(r"[^\W\d_][\w'’.&-]*", line)  # noqa: RUF001
           if word.casefold() not in _SMALL]
    return bool(big) and all(any(char.isupper() for char in word) for word in big)


def _entries(body: list[tuple[str, bool]], *, single: bool) -> tuple[Entry, ...]:
    """Entries, anchored on the lines that carry dates.

    Each dated line is one entry. Its title is on that line, or on the one or two capitalised lines
    just above it, or just below — the three ways CVs are laid out. Everything else until the next
    entry is its bullets, glyph or not, because python-docx returns Word's bullets as plain lines.
    """
    dated = {index: found for index, (line, bullet) in enumerate(body)
             if not bullet and (found := _dates(line, single=single))}
    owner: dict[int, int] = {index: index for index in dated}
    for anchor in dated:
        if _has_title_word(_without_dates(body[anchor][0])):
            continue
        above = anchor - 1
        while anchor - above <= 2 and above >= 0 and above not in owner and _header_like(*body[above]):
            owner[above] = anchor
            above -= 1
    for anchor in dated:
        below = anchor + 1
        while below - anchor <= 2 and below < len(body) and below not in owner and _header_like(
                *body[below]):
            owner[below] = anchor
            below += 1

    starts = sorted((min(index for index, who in owner.items() if who == anchor), anchor)
                    for anchor in dated)
    entries = []
    for position, (start, anchor) in enumerate(starts):
        stop = starts[position + 1][0] if position + 1 < len(starts) else len(body)
        heads = [_without_dates(body[index][0]) for index in range(start, stop) if owner.get(index) == anchor]
        bullets = tuple(body[index][0] for index in range(start, stop) if index not in owner)
        written, first, last = dated[anchor]
        entries.append(_entry(heads, written, first, last, bullets))
    return tuple(entries)


def _entry(heads: list[str], dates: str, start: int | None, end: int | None,
           bullets: tuple[str, ...]) -> Entry:
    parts = [part.strip() for head in heads for part in _PARTS.split(head) if part.strip(" ()")]
    title = next((part for part in parts if _has_title_word(part)),
                 next((part for part in parts if _DEGREE.search(part)), parts[0] if parts else ""))
    rest = [part for part in parts if part is not title]
    places = [part for part in rest if _is_place(part)]
    organisation = next((part for part in rest if part not in places), rest[0] if rest else "")
    return Entry(title=title, organisation=organisation, heading=" — ".join([title, *rest]).strip(" —"),
                 dates=dates, start=start, end=end, bullets=bullets)


def _is_place(text: str) -> bool:
    return fold(text) in ("remote", "hybrid") or (
        len(text.split()) <= 2 and (read_country(text) is not None or bool(_PLACES.search(fold(text)))))


# ── the facts ────────────────────────────────────────────────────────────────────────────────

def read(documents: dict[str, str], *, today: date | None = None) -> Facts:
    """Everything their documents say about them, keyed by document kind (`cv`, `cover_letter`, ...).

    The CV and a LinkedIn export are read in full. A cover letter and their own "about you" text only
    fill in contact details the CV lacks, and say how many years they have worked: their prose
    mentions places and languages for every reason except to say where they are or what they speak.
    """
    cv = read_cv("\n\n".join(documents.get(kind) or "" for kind in ("cv", "linkedin")))
    flat = [(line, "header") for line in cv.header] + [
        (line, section.kind) for section in cv.sections for line in section.lines]
    # Who and where they are: the header, and a contact section if the CV has one.
    zone = [line for line, kind in flat if kind in ("header", "contact")]
    email = _email(list(cv.header), [(section.kind, list(section.lines)) for section in cv.sections])
    local = fold((email or "@").split("@")[0])
    names = [line for line in cv.header if _name_like(line)]
    prose = [documents.get(kind) or "" for kind in ("cover_letter", "history")]
    prose_lines = [line for text in prose for line in text.splitlines()]

    inline = {kind: [match.group(2) for line, _ in flat
                     if (match := re.match(r"^(.{2,30}?)\s*:\s*(.+)$", line))
                     and heading_kind(match.group(1)) == kind] for kind in ("skills", "languages")}
    jobs = sorted((entry for section in cv.of("experience") for entry in section.entries),
                  key=lambda entry: (entry.start is not None, entry.end is None,
                                     entry.end or 0, entry.start or 0), reverse=True)
    stated = [int(found) for text in (*_lines_of(cv, "profile"), *prose)
              for found in _STATED_YEARS.findall(text) if int(found) <= 50]

    return Facts(
        name=_title_case(next((line for line in names if _agrees(line, local)), names[0]))
        if names else None,
        email=(email or _first(EMAIL, prose_lines) or "").lower() or None,
        phone=_phone(zone) or _phone([line for line, _ in flat if _PHONE_LABEL.search(line)])
        or _phone([line for line in prose_lines if _PHONE_LABEL.search(line)]),
        links=_unique(link.rstrip(").,;:") for line in [*(line for line, _ in flat), *prose_lines]
                      for link in _LINK.findall(line)),
        location=next((part.strip() for line in zone for part in re.split(r"[|·•]", line)
                       if "@" not in part and not re.search(r"\d", part) and len(part.split()) <= 4
                       and (read_country(part) is not None or _PLACES.search(fold(part)))), None),
        places=_unique(_PLACE_NAMES[found] for line in zone for found in _PLACES.findall(fold(line))),
        languages=_languages([*_lines_of(cv, "languages"), *inline["languages"]]),
        jobs=tuple(jobs),
        skills=_skills([*_lines_of(cv, "skills"), *inline["skills"]]),
        years=max([*stated, *_experience_years(jobs, today or date.today())], default=None),
        cv=cv,
    )


def _lines_of(cv: CV, kind: str) -> list[str]:
    return [line for section in cv.of(kind) for line in section.lines]


def _first(pattern: re.Pattern, *groups: list[str]) -> str | None:
    return next((found.group(0) for lines in groups for line in lines
                 if (found := pattern.search(line))), None)


def _unique(items) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for item in items:
        if item:
            seen.setdefault(fold(item).rstrip("/"), item)
    return tuple(seen.values())


def _email(header: list[str], sections: list[tuple[str, list[str]]]) -> str | None:
    """Theirs: from the header or a contact section first, and never from their references."""
    return _first(EMAIL, header, *[lines for kind, lines in sections if kind == "contact"],
                  *[lines for kind, lines in sections if kind != "other"])


def _agrees(line: str, local: str) -> bool:
    """Whether a name-shaped line shares a word with the part of their email before the @."""
    return any(len(word) >= 3 and fold(word) in local
               for word in re.findall(r"[^\W\d_]+", _name_part(line)))


def _name_part(line: str) -> str:
    return re.split(rf"\s+[{_DASH}|·•]\s+|,\s*|\s*\|\s*", line)[0].strip()


def _name_like(line: str) -> bool:
    part = _name_part(line)
    tokens = part.split()
    big = [token for token in tokens if token.casefold() not in _SMALL]
    return (2 <= len(tokens) <= 5 and 2 <= len(big) <= 4
            and all(token[0].isupper() and re.fullmatch(r"[^\W\d_]+(?:['’.-][^\W\d_]*)*", token)  # noqa: RUF001
                    for token in big)
            and not _has_title_word(part) and heading_kind(part) is None and not _is_place(part)
            and fold(part) not in ("curriculum vitae", "resume", "résumé"))


def _title_case(line: str) -> str:
    part = _name_part(line)
    return part.title() if part.isupper() else part


def _phone(lines: list[str]) -> str | None:
    for line in lines:
        for found in _PHONE.finditer(line):
            digits = re.sub(r"\D", "", found.group())
            if (9 <= len(digits) <= 15 and not _RANGE.search(found.group())
                    and not re.fullmatch(r"(?:(?:19|20)\d\d\D*)+", found.group())):
                return found.group().strip()
    return None


def _languages(lines: list[str]) -> tuple[str, ...]:
    """Language codes in the order written, leaving out any they say they speak only a little."""
    found: list[str] = []
    for line in lines:
        for segment in re.split(r"[,;|·•/]", line):
            folded = fold(segment)
            for code, names in LANGUAGE_NAMES.items():
                if code not in found and not _LOW_LEVEL.search(segment) and any(
                        re.search(rf"(?<!\w){re.escape(fold(name))}(?!\w)", folded) for name in names):
                    found.append(code)
    return tuple(found)


def _skills(lines: list[str]) -> tuple[str, ...]:
    """Each skill once, as written: "Languages & Frameworks: Python, Go" gives Python and Go."""
    items = []
    for line in lines:
        for item in re.split(rf"[,;•·|]|\s[{_DASH}]\s", re.sub(r"^[^:]{2,40}:\s*", "", line)):
            item = re.sub(r"\(.*?\)", "", item).strip(" .")  # noqa: PLW2901 - the cleaned item
            if 2 <= len(item) <= 40 and len(item.split()) <= 4 and re.search(r"[^\W\d_]", item):
                items.append(item)
    return _unique(items)


def _experience_years(jobs: list[Entry], today: date) -> list[int]:
    """Whole years covered by their dated jobs, overlaps counted once. Empty when none are dated."""
    now = today.year * 12 + today.month - 1
    spans = sorted((job.start, now if job.end is None else job.end) for job in jobs
                   if job.start is not None)
    if not spans:
        return []
    months, reach = 0, None
    for start, end in spans:
        begin = start if reach is None else max(start, reach)
        months += max(0, end - begin)
        reach = end if reach is None else max(reach, end)
    return [months // 12]
