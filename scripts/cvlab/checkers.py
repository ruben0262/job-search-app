"""Checkers that are not ours: what other companies' tools make of a CV or letter we wrote.

Our checks (`services/docgen/checks.py`, `pdf.inspect_pdf`) and the judge read documents by rules we chose,
so a prompt can learn to pass them. These are tools job seekers already use, scoring by rules we did not
write. The owner has the platforms' permission to script them (2026-11-14). Neither is an ATS: ResyMatch
is one company's model of what an ATS and a recruiter look for, and the letter checker is a published
formula, scored in the page by its own script. Read them as outside opinions, each with its own quirks,
never as one number to push up: a match rate rises with keyword stuffing too.

- `resymatch`, for CVs: Cultivated Culture's ResyMatch. Our PDF goes up as a person would upload it (their
  own CV, which we hold as text, is pasted), with the posting's title and text. Back come its match rate,
  its ATS best-practice, hard-skill, soft-skill and "sales" scores, what its reader took for the email and
  phone, and the posting's skills it found missing.
- `letter`, for cover letters: ATS Verification's cover letter checker. It scores in the browser, so the
  letter never leaves this computer: strength out of 100, the share of the posting's 20 most used words
  the letter uses, and how much of it is "I/my" rather than "you/your".
- `affinda`, for CVs: Affinda's resume parser, the kind of reader an applicant tracking system puts a CV
  through, over its API on the owner's key (`certificates/affinda.env`). It gives no score. It says what
  it read: the name, the email and phone, and each job and qualification with its employer and dates.
  Those are compared with what the CV says, field by field. The file is deleted there once it is read.

Every answer is kept beside its document (`<document>.<checker>.json`) and never asked for again: the
tools are free and limited, and an unchanged document gets the same answer.
"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import pathlib
import re
from concurrent import futures
from dataclasses import dataclass

logger = logging.getLogger("cvlab")

RESYMATCH = "https://cultivatedculture.com/resume-scanner/"
LETTER = "https://atsverification.com/tools/cover-letter-checker/"
# The region the owner's key was made in; `AFFINDA_HOST` names another.
AFFINDA = "https://api.eu1.affinda.com"

# One answer is a page load, an upload and a scan: about 20 seconds when the site is well. Past this the
# site is treated as down for this document and the row goes without it.
TIMEOUT_MS = 120_000


@dataclass(frozen=True)
class Document:
    """One document as a person would hand it to a checker, with what is known to be true about it."""

    kind: str               # "cv" or "cover"
    path: pathlib.Path      # where it is kept; its answers are kept beside it
    text: str               # what a person would paste
    pdf: bytes | None       # what a person would upload; None for their own document, which we hold as text
    title: str              # the posting's title
    posting: str            # the posting's text
    theirs: str             # all they gave us: a skill the CV lacks that is in here was left out
    email: str = ""         # what the header says, to see whether the checker read it so
    phone: str = ""
    name: str = ""
    # Each job and qualification a written CV lists: what it is, where, and its dates as written.
    entries: tuple[tuple[str, str, str], ...] = ()


def measure(names: list[str], document: Document) -> dict[str, float | None]:
    """Each named checker's numbers for one document, as `<checker>.<measure>`: kept answers first."""
    scores: dict[str, float | None] = {}
    for name in names:
        checker, kind = CHECKERS[name]
        if kind != document.kind:
            continue
        kept = document.path.with_name(f"{document.path.name}.{name}.json")
        if kept.exists():
            answer = json.loads(kept.read_text(encoding="utf-8"))
        else:
            try:
                # Playwright's objects belong to the thread that made them, and the bench writes cells on
                # several: every checker runs on this one thread, one document at a time.
                answer = _WORKER.submit(checker, document).result()
            except Exception as exc:  # noqa: BLE001 - one site failing is that row without it, not the grid
                # The first line only: Playwright's message goes on to a call log quoting what was typed,
                # which is the person's CV or letter (§8).
                logger.warning("checker %s failed on %s, so that row goes without it: %s: %s", name,
                               document.path.name, type(exc).__name__, next(iter(str(exc).splitlines()), ""))
                continue
            kept.write_text(json.dumps(answer, indent=1, ensure_ascii=False), encoding="utf-8")
        scores.update({f"{name}.{key}": value for key, value in answer["scores"].items()})
    return scores


def close() -> None:
    """Shut the checkers' browser, if one was opened. The next answer asked for opens another."""
    _WORKER.submit(_close).result()


# ── ResyMatch ─────────────────────────────────────────────────────────────────────────────────

def resymatch(document: Document) -> dict:
    """One CV and its posting through ResyMatch, as a person would scan them."""
    page = _page()
    try:
        page.goto(RESYMATCH, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        if document.pdf is None:
            # The paste box is hidden behind "Click here to paste text" until it is clicked, as a person does.
            page.click("text=Click here to paste text")
            page.fill("textarea[name=resume_content]", document.text)
        else:
            with page.expect_response(lambda r: "/app/ajax/UploadResume" in r.url, timeout=TIMEOUT_MS):
                page.set_input_files("input[name=userfile]", files=[
                    {"name": "cv.pdf", "mimeType": "application/pdf", "buffer": document.pdf}])
        page.fill("input[name=job_title]", job_title(document.title))
        page.locator("textarea[name=job_description]").first.fill(document.posting)
        # The scan's answer is read on its way to the page. The page leaves for its results as soon as it
        # has it, and an answer read after that is gone: every scan failed so from 2026-11-15, when the
        # site began answering the scan button itself (`GenerateScan`) where the results used to ask
        # (`FetchScan`).
        scans: list[dict] = []

        def through(route) -> None:
            response = route.fetch(timeout=TIMEOUT_MS)
            with contextlib.suppress(ValueError):
                scans.append(response.json().get("data") or {})
            route.fulfill(response=response)

        page.route(re.compile(r"/app/ajax/(?:Generate|Fetch)Scan"), through)
        page.click("text=START RESUME SCAN")
        page.wait_for_url("**scanId=**", timeout=TIMEOUT_MS)
        scan = next((each for each in scans if each.get("match_rate") not in (None, "")), None)
        if scan is None:
            raise RuntimeError("ResyMatch answered the scan with no match rate")
        return read_resymatch(scan, document)
    finally:
        # The results page goes on asking; what it asks after this is nobody's to wait for.
        page.unroute_all(behavior="ignoreErrors")
        page.close()


# What each kind of row was called when the answer came already sorted (`FetchScan`, until 2026-11-15).
_SORTED = {"Ats": "ats_data", "Hard Skill": "hard_data", "Soft Skill": "soft_data", "Sales": "sales_data"}


def _rows(data: dict, category: str) -> list[dict]:
    """ResyMatch's rows of one kind: what it read of the CV ("Ats"), and each of the posting's hard and
    soft skills with how often the CV says it. The scan button's own answer carries them all in one list,
    as text (`scan_results`); the older answer had a list for each kind."""
    if data.get(_SORTED[category]) is not None:
        return list(data[_SORTED[category]])
    try:
        rows = json.loads(data.get("scan_results") or "[]")
    except ValueError:
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("category") == category]


# A contract or its hours, said before the title: "Bank Staff Nurse" is a staff nurse on the bank, and
# the employer's own page calls the job "Staff Nurse" (the one posting of 18 whose title no CV carried,
# 2026-11-15: the writer leaves the contract off, as its prompt says, and so does this). "Bank" only
# before "Staff": a Bank Manager manages a bank.
_CONTRACT_FIRST = re.compile(r"(?i)^\s*(?:(?:bank(?=\s+staff\b)|temporary|temp|permanent|seasonal|casual|"
                             r"part[- ]time|full[- ]time|fixed[- ]term)\s+)+")


# "m/f/d", "M/W/D": who may apply, which is no part of the title and whose strokes are not its end.
_WHO_MAY_APPLY = re.compile(r"(?i)\(?\b[mfwdx](?:/[mfwdx]){1,3}\b\)?")
# The little words between a title's own, in the postings' languages: "Clinical Nurse Specialist for
# Endometriosis" names "Clinical Nurse Specialist Endometriosis".
_LITTLE = frozenset({"the", "and", "or", "of", "for", "in", "at", "to", "with", "on",  # English
                     "und", "für", "im", "der", "die", "das", "des", "dem", "den", "mit", "von", "zur", "zum",
                     "bei", "en", "voor", "van", "het", "met",  # German, Dutch
                     "di", "del", "della", "per", "al", "alla", "az", "és",  # Italian, Hungarian
                     "et", "pour", "la", "le", "les", "du", "para", "el", "los", "las"})  # French, Spanish


def job_title(line: str) -> str:
    """The job's title as a person would type it into a checker: a board's line without its hours, its
    contract and its place ("Retail Advisor, 38hrs 12M FTC, Aintree" is "Retail Advisor", "Bank Staff
    Nurse/ODP" is "Staff Nurse"). ResyMatch looks for the title in the CV, and found the whole line in
    none (7 of 9 CVs, 2026-11-14)."""
    line = _WHO_MAY_APPLY.sub(" ", line)
    title = re.split(r"\s+[-\u2013|]\s+|[,(/]", line, maxsplit=1)[0].strip() or line.strip()
    return _CONTRACT_FIRST.sub("", title).strip() or title


def names_title(text: str, title: str) -> bool:
    """Whether a letter names the job roughly as the posting does (owner, 2026-11-19: "perhaps not the exact
    title but it should be in there roughly the same"). The title is cut as `job_title` cuts it, except that
    either side of a stroke will do ("Elektriker / Elektroniker"); its little words do not count. It has to
    be in one sentence of the letter or its subject, its words in their order with at most one other word
    between two of them, each word as it is or in a form of it ("Mitarbeiterin" for "Mitarbeiter"): all of a
    title of up to three words, three in five of a longer one ("Mitarbeiterin im Kundenservice für
    Krankenkassen" for "... Krankenkassen und weitere Themen"). It is cut at "m/f/d" too, as at a dash: what
    follows says where or with what ("Elektriker / Elektroniker (m/w/d) im Bereich Gebäudetechnik")."""
    line = re.split(r"\s+[-\u2013|]\s+|[,(]", _WHO_MAY_APPLY.sub(",", title), maxsplit=1)[0]
    line = re.sub(r"\s*/\s*", "/", _CONTRACT_FIRST.sub("", line).casefold())
    own = [options for options in ({word for part in token.split("/") for word in re.findall(r"[^\W_]+", part)
                                    if len(word) > 1 and word not in _LITTLE} for token in line.split())
           if options]
    need = len(own) if len(own) <= 3 else math.ceil(0.6 * len(own))
    # A title has no comma or bracket in it once cut, so a phrase that names it has none either.
    for sentence in re.split(r"(?<=[.!?])\s+|[\n,;:()]+", text.casefold()):
        words = [word for word in re.findall(r"[^\W_]+", sentence) if len(word) > 1 and word not in _LITTLE]
        # The longest run of the title's words ending at each place: (title word, letter word) -> length.
        run: dict[tuple[int, int], int] = {}
        for at, options in enumerate(own):
            for where, word in enumerate(words):
                if any(_form_of(word, each) for each in options):
                    run[at, where] = 1 + max((length for (before, there), length in run.items()
                                              if before < at and 0 < where - there <= 2), default=0)
        if own and max(run.values(), default=0) >= need:
            return True
    return False


def _form_of(word: str, own: str) -> bool:
    """`word` is `own`, or `own` with its last letter kept or changed and up to three added ("mitarbeiterin",
    "pflegefachassistentin", "nurses"). Not a shorter word, nor a longer one: "reception" does not name a
    receptionist, nor "management" a manager."""
    return word == own or (len(own) >= 4 and word.startswith(own[:-1]) and len(word) - len(own) + 1 <= 3)


def read_resymatch(data: dict, document: Document) -> dict:
    """ResyMatch's answer as numbers, with what its reader took the CV to say."""
    read = {row["meta_key"]: row["meta_value"] for row in _rows(data, "Ats")}
    missing = [row["meta_key"] for row in _rows(data, "Hard Skill") + _rows(data, "Soft Skill")
               if not int(row.get("resume_count") or 0)]
    left_out = [skill for skill in missing if _theirs(skill, document.theirs)]
    scores = {"match": _number(data, "match_rate"), "ats": _number(data, "ats_best_practices"),
              "hard": _number(data, "hard_skills_match"), "soft": _number(data, "soft_skills_match"),
              "sales": _number(data, "sales_index"), "left_out": len(left_out)}
    if document.email:
        # Whether the address and number a recruiter would use came out as written. A reader that ran the
        # phone into the email (2026-11-14: "900123maya.example@example.com") lost the way to reach them.
        scores["contact_read"] = float(_same_email(read.get("emails"), document.email)
                                       and _same_phone(read.get("phoneNumbers"), document.phone))
    return {"scores": scores, "left_out": left_out, "missing": missing, "read": read, "raw": data}


# ── the cover letter checker ──────────────────────────────────────────────────────────────────

# What the page shows, read back. Its formula is its own script (`page-*.js`, read 2026-11-14): 15 for a
# length of 200-450 words, 15 for no stock phrase, 8 greeting, 7 a first paragraph of 15+ words, 7 a last
# one that signs off, 3 three paragraphs or more, 15 a number, 10 "I/my" at most 65% of I/my/you/your,
# 5 sentences of 12-24 words, 5 three to five paragraphs, 10 for 60% of the posting's 20 commonest words.
_SHOWN = {"score": r"(\d+)\s*/ 100 cover letter strength score", "words": r"Length: (\d+) words",
          "paragraphs": r"Paragraph count: (\d+)", "i_share": r"Pronoun balance: (\d+)% \"I/my\"",
          "keywords": r"Job description keyword match: (\d+)%",
          "sentence_words": r"Avg sentence length: ([\d.]+) words"}


def letter(document: Document) -> dict:
    """One letter and its posting through the cover letter checker, which scores in the page."""
    page = _page()
    try:
        page.goto(LETTER, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        page.fill("#letter-input", document.text)
        page.fill("#jd-input", document.posting)
        page.wait_for_selector("text=cover letter strength score", timeout=TIMEOUT_MS)
        return read_letter(page.inner_text("main"))
    finally:
        page.close()


def read_letter(shown: str) -> dict:
    """The checker's result panel as numbers, with the checks it failed and the posting's words it missed."""
    scores = {}
    for key, pattern in _SHOWN.items():
        found = re.search(pattern, shown)
        scores[key] = float(found.group(1)) if found else None
    weak = re.search(r"Weak/clich\u00e9d phrases:\s*(\d+) flagged", shown)
    scores["stock_phrases"] = float(weak.group(1)) if weak else 0.0
    missing = re.search(r"Missing \(consider adding\):\s*([^\n]+)", shown)
    return {"scores": scores, "failed": re.findall(r"\u2717 ([^\n]+)", shown),
            "missing": [each.strip() for each in missing.group(1).split(",")] if missing else [],
            "shown": shown}


# ── Affinda ───────────────────────────────────────────────────────────────────────────────────

def affinda(document: Document) -> dict:
    """One CV through Affinda's resume parser, as an employer's system would receive it: our PDF (their
    own CV, which we hold as text, as a text file). Read there and deleted as soon as it is."""
    import httpx  # noqa: PLC0415 - only a run with this checker needs it

    key, collection = os.environ.get("AFFINDA_API_KEY"), os.environ.get("AFFINDA_COLLECTION")
    if not key or not collection:
        raise RuntimeError("AFFINDA_API_KEY and AFFINDA_COLLECTION are not set (certificates/affinda.env)")
    file = (("cv.pdf", document.pdf, "application/pdf") if document.pdf is not None
            else ("cv.txt", document.text.encode("utf-8"), "text/plain"))
    answer = httpx.post(f"{os.environ.get('AFFINDA_HOST', AFFINDA)}/v3/documents",
                        headers={"Authorization": f"Bearer {key}"}, files={"file": file},
                        data={"collection": collection, "wait": "true", "deleteAfterParse": "true"},
                        timeout=TIMEOUT_MS / 1000)
    if answer.is_error:
        # Its body says why ("no credits left", "file not supported"); the status alone does not. One line.
        raise RuntimeError(f"Affinda answered {answer.status_code}: {' '.join(answer.text.split())[:300]}")
    return read_affinda(answer.json().get("data") or {}, document)


def read_affinda(data: dict, document: Document) -> dict:
    """What the parser read, against what the CV says: the name, the email and phone, and each job and
    qualification, read when the parser has one with both its title and its employer, its dates read when
    that one starts in the same year and is still held or not as written."""
    jobs = [(row.get("jobTitle"), row.get("organization"), row.get("dates") or {})
            for row in data.get("workExperience") or []]
    studies = [((row.get("accreditation") or {}).get("inputStr"), row.get("organization"),
                row.get("dates") or {}) for row in data.get("education") or []]
    scores: dict[str, float | None] = {"jobs": float(len(jobs)),
                                       "skills": float(len(data.get("skills") or []))}
    if document.name:
        scores["name_read"] = float(_same((data.get("name") or {}).get("raw"), document.name))
    if document.email:
        scores["contact_read"] = float(
            _same_email(" ".join(data.get("emails") or []), document.email)
            and _same_phone(",".join(data.get("phoneNumbers") or []), document.phone))
    found = [next((dates for what, where, dates in jobs + studies
                   if _within(what, title) and _within(where, employer)), None)
             for title, employer, _ in document.entries]
    if document.entries:
        scores["entries_read"] = sum(dates is not None for dates in found) / len(document.entries)
    # The ones the CV gives dates to: a job the person gave no dates for is read into the one above it,
    # which is theirs to mend and not the writer's (owner, 2026-11-15: steer by what does not depend on
    # the person).
    with_dates = [dates for (_, _, written), dates in zip(document.entries, found, strict=True) if written]
    if with_dates:
        scores["dated_entries_read"] = sum(dates is not None for dates in with_dates) / len(with_dates)
    dated = [(written, dates) for (_, _, written), dates in zip(document.entries, found, strict=True)
             if written and dates is not None]
    if dated:
        scores["dates_read"] = sum(_same_dates(written, dates) for written, dates in dated) / len(dated)
    return {"scores": scores, "read": {"name": data.get("name"), "emails": data.get("emails"),
                                       "phones": data.get("phoneNumbers"), "jobs": jobs, "studies": studies,
                                       "location": (data.get("location") or {}).get("formatted")},
            "raw": data}


CHECKERS = {"resymatch": (resymatch, "cv"), "letter": (letter, "cover"), "affinda": (affinda, "cv")}


# ── shared ────────────────────────────────────────────────────────────────────────────────────

_WORKER = futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="checker")
_OPEN: dict = {}


def _browser():
    if "browser" not in _OPEN:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 - only a run with checkers needs it

        _OPEN["playwright"] = sync_playwright().start()
        _OPEN["browser"] = _OPEN["playwright"].chromium.launch()
    return _OPEN["browser"]


def _page():
    """A page that waits for each step as long as the bench waits for a site (`TIMEOUT_MS`), not the
    browser's own 30 seconds: a second clock, and the shorter one decided."""
    page = _browser().new_page()
    page.set_default_timeout(TIMEOUT_MS)
    return page


def _close() -> None:
    if "browser" in _OPEN:
        _OPEN.pop("browser").close()
        _OPEN.pop("playwright").stop()


def _number(data: dict, key: str) -> float | None:
    value = data.get(key)
    return float(value) if value not in (None, "") else None


_SUFFIX = re.compile(r"(?:ations?|ions?|ments?|ing|ers?|ed|es|s|e)$")


def _theirs(skill: str, documents: str) -> bool:
    """Whether every word of a skill, give or take its ending, is somewhere in what they gave us. A short
    word must be found whole: "BI" is not the start of "billing"."""
    def found(word: str) -> bool:
        stem = _SUFFIX.sub("", word)
        pattern = rf"(?<!\w){re.escape(stem)}" if len(stem) >= 4 else rf"(?<!\w){re.escape(word)}(?!\w)"
        return re.search(pattern, documents, re.IGNORECASE) is not None

    words = re.findall(r"[a-z0-9+#]+", skill.lower())
    return bool(words) and all(found(word) for word in words)


def _flat(text: str | None) -> str:
    """Its letters and figures alone: a parser tidies what it reads ("St. Brendans" for "St Brendan's",
    "Tearooms" for "Tea Rooms", "Beans & Greens" for "Beans and Greens"), and that is the same name."""
    return "".join(re.findall(r"[^\W_]+", (text or "").casefold().replace("&", " and ")))


def _same(read: str | None, written: str) -> bool:
    return bool(_flat(read)) and _flat(read) == _flat(written)


def _within(read: str | None, written: str) -> bool:
    """Whether what was read is what was written, give or take what a parser trims or leaves on its end."""
    one, other = _flat(read), _flat(written)
    return len(one) >= 3 and len(other) >= 3 and (one in other or other in one)


_PRESENT = re.compile(r"(?i)\b(?:present|current|now|today|since)\b")


def _same_dates(written: str, read: dict) -> bool:
    """A start in the year the CV gives first, written whole or short ("Since March 23"), and still held
    or not as the CV says."""
    year = re.search(r"\d{4}", written) or re.search(r"(?<!\d)\d{2}(?!\d)(?!.*\d{2})", written)
    started = str(read.get("startDate") or read.get("completionDate") or "")[:4]
    held = bool(_PRESENT.search(written))
    return bool(year) and started.endswith(year.group(0)) and bool(read.get("isCurrent")) == held


def _same_email(read: str | None, written: str) -> bool:
    return written.lower() in {each.lower() for each in re.split(r"[\s,;]+", read or "") if each}


def _same_phone(read: str | None, written: str) -> bool:
    """The same number, whether read as written or with its country's code ("+491713920047" for
    "0171 39200 47")."""
    if not written:
        return True
    digits = re.sub(r"\D", "", written).lstrip("0")
    return any(len(digits) >= 7 and re.sub(r"\D", "", each).endswith(digits)
               for each in re.split(r"[,;]", read or ""))
