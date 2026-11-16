"""The CV lab: run writing-prompt variants over the same postings and compare the numbers.

Nothing here writes to a prompt. Variant `live` reads `libs/prompts/write_cv.md` (`--kind cover`:
`write_cover.md`) at run time, so the baseline is always the prompt every account is written with and
cannot drift from it; every other variant is a separate file under `variants/` (a letter's under
`variants/cover/`), filled the same way (`build.prepare`). Variant `uploaded` is the person's own CV or
letter as they uploaded it, with no call: what they would have sent without us. `CLAUDE.md` §3a: the live
prompt is measured, never edited, and a variant becomes the live prompt only when the owner says so.

`--checkers` adds what tools that are not ours make of each document (`checkers.py`): for a CV ResyMatch
and Affinda's resume parser (`certificates/affinda.env`), for a letter a cover letter checker. Their
numbers join the comparison beside ours.

    # the made-up panel (`panel/README.md`): eleven people who are not in IT, two postings each
    python scripts/cvlab/run.py --candidates-dir scripts/cvlab/panel --candidates nurse,warehouse,chef \
        --account someone@example.com --samples 2 --out scripts/cvlab/out/panel

    # score CVs already on disk — no calls, no credential, no money
    python scripts/cvlab/run.py --dry-run --out scripts/cvlab/out/ab

    # the new prompt against the CV they uploaded, on the account they linked on this computer
    python scripts/cvlab/run.py --candidates ruben=db:someone@example.com --account someone@example.com \
        --variants uploaded,live --baseline uploaded --samples 3 --judge --out scripts/cvlab/out/first

    # letters already written, and what the outside checker makes of them and of their own
    python scripts/cvlab/run.py --kind cover --dry-run --variants uploaded,live --baseline uploaded \
        --checkers letter --out scripts/cvlab/out/first

A candidate is a folder under `candidates/` (never in git: real people's documents) or `NAME=db:EMAIL`,
an account in the local database (`MONGODB_URI`, never Atlas), read as the docgen worker reads it. NAME
picks the postings: each fixture names the candidate it is for. Credential: `--account EMAIL`, the AI
account that person has in use there (a Claude sign-in runs Claude Code), else `--key-file` or
`ANTHROPIC_API_KEY`.

**Why samples and pairing.** One generation is not a result: the model varies run to run, so a
single CV per cell cannot tell a better prompt from a luckier one. Every cell is run `--samples`
times, differences are taken *paired* — same candidate, same posting, baseline minus variant — and
the spread is reported beside the mean. Postings carry a `group` and all copies of a group stay on
the same side of any split, because this platform's corpus lists one role in a dozen cities and each
copy has its own `content_key`.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import math
import os
import pathlib
import re
import statistics
import sys
import threading
import time
import unicodedata
from concurrent import futures
from html import unescape
from types import SimpleNamespace

from pypdf import PdfReader

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from libs import templates  # noqa: E402
from libs.documents.needs import BASE  # noqa: E402
from libs.llm import cli, client_for  # noqa: E402
from libs.settings import CV_MODEL, DEFAULT_DOCUMENT_MODELS  # noqa: E402
from scripts.cvlab import checkers, cvchecks  # noqa: E402
from services.docgen import build, pdf  # noqa: E402

logger = logging.getLogger("cvlab")

HERE = pathlib.Path(__file__).resolve().parent
LIVE_PROMPTS = {kind: REPO / "libs" / "prompts" / name for kind, name in build.PROMPTS.items()}
VARIANTS = {"cv": HERE / "variants", "cover": HERE / "variants" / "cover"}
JUDGE_PROMPT = REPO / "libs" / "prompts" / "eval_cv.md"
# The letters' reviewer: the bench's own, as the CV's reviewer was before it was approved (owner, 2026-11-16:
# "cover letter prompt should be claude first same way cv was made, with the same procedure").
LETTER_JUDGE_PROMPT = HERE / "eval_cover.md"
LETTER_JUDGE_SYSTEM = "You review cover letters the way the hiring manager for the job would."
# What is known about each posting's company, a file a posting (`company/<slug>.txt`): a `{{company}}` slot
# in a letter's wording is filled with it, and the letters' reviewer is shown it whatever wrote the letter.
COMPANY_FACTS = HERE / "company"
COMPANY_SLOT = "{{company}}"
# The person's own CV, unchanged: the baseline every written CV is measured against.
UPLOADED = "uploaded"

# Held fixed while the writing prompt varies: the judge is the instrument, not the thing measured.
JUDGE_MODEL = "claude-sonnet-5-5"
JUDGE_SYSTEM = "You review CVs the way the recruiter for the job would."

# Seven dimensions, each with a quoted line, plus three fixes. coverlab lost half a grid to a judge
# reply truncated mid-JSON that scored as `None` and was quietly averaged over, so this is generous.
JUDGE_MAX_TOKENS = 6_000


class Posting:
    """One job fixture, shaped like the corpus row `build.prepare` reads."""

    def __init__(self, data: dict):
        self.slug = data["slug"]
        self.group = data["group"]
        self.candidate = data["candidate"]
        self.id = data.get("id", 0)
        self.url = data.get("url", "")
        self.title = data.get("title", "")
        self.company = data.get("company", "")
        self.location = data.get("location", "")
        self.source = data.get("source", "")
        self.description = data.get("description", "")
        self.workplace = data.get("workplace") or ""
        self.language = data.get("language")
        facts = COMPANY_FACTS / f"{self.slug}.txt"
        self.company_facts = facts.read_text(encoding="utf-8").strip() if facts.exists() else ""


class Candidate:
    """One person: who they are and what they uploaded, by kind, as `documents_of` gives it.

    A folder holds `candidate.json` (`{"name", "links", "languages"}`) and one text file a document,
    named for its kind: `cv.txt`, `cv-2.txt`, `cover_letter.txt`, `history.txt` (their "About you")."""

    def __init__(self, spec: str, root: pathlib.Path):
        self.name, _, source = spec.partition("=")
        if source.startswith("db:"):
            self._from_db(source[3:])
        else:
            self._from_folder(root / self.name)
        if not self.documents.get("cv"):
            raise SystemExit(f"candidate {self.name} has no CV")
        self.user = SimpleNamespace(full_name=self.identity.get("name"),
                                    links=self.identity.get("links") or [])

    def _from_folder(self, folder: pathlib.Path) -> None:
        if not folder.is_dir():
            raise SystemExit(f"no candidate at {folder}: a folder of their documents, or NAME=db:EMAIL")
        self.identity = json.loads((folder / "candidate.json").read_text(encoding="utf-8"))
        self.documents: dict[str, list[str]] = {}
        for path in sorted(folder.glob("*.txt")):
            self.documents.setdefault(re.sub(r"-\d+$", "", path.stem), []).append(
                path.read_text(encoding="utf-8"))
        self.languages = tuple(self.identity.get("languages") or ())

    def _from_db(self, email: str) -> None:
        from libs.db import database  # noqa: PLC0415 - only a database candidate needs one
        from libs.suggestions import documents_of  # noqa: PLC0415
        from libs.tenant.repository import TenantRepository  # noqa: PLC0415

        db = database()
        row = db.users.find_one({"email": email})
        if row is None:
            raise SystemExit(f"no account {email} in {os.environ.get('MONGODB_DB')}")
        self.identity = {"name": row.get("full_name"), "links": row.get("links") or []}
        self.documents = documents_of(db, row["_id"])
        self.languages = tuple((TenantRepository(db).get_config(row["_id"]) or {}).get("languages") or ())


def load_postings(root: pathlib.Path, candidate: str | None, only: list[str] | None) -> list[Posting]:
    postings = [Posting(json.loads(path.read_text(encoding="utf-8")))
                for path in sorted(root.glob("*.json"))]
    if candidate:
        postings = [p for p in postings if p.candidate == candidate]
    if only:
        wanted = set(only)
        postings = [p for p in postings if p.slug in wanted]
        missing = wanted - {p.slug for p in postings}
        if missing:
            raise SystemExit(f"no such posting fixture: {', '.join(sorted(missing))}")
    if not postings:
        raise SystemExit(f"no job fixtures under {root}")
    return postings


def load_variant(name: str, kind: str = "cv") -> str | None:
    """`live` is the prompt every account is written with, read fresh; `uploaded` has none. Anything else
    is a file under `variants/`, or `variants/cover/` for a letter."""
    if name == UPLOADED:
        return None
    path = LIVE_PROMPTS[kind] if name == "live" else VARIANTS[kind] / f"{name}.md"
    if not path.exists():
        raise SystemExit(f"variant {name!r} not found at {path}")
    return path.read_text(encoding="utf-8")


def credential(key_file: str | None) -> tuple[str, str]:
    """The API key and its kind. The value is never logged."""
    value = (pathlib.Path(key_file).read_text(encoding="utf-8") if key_file
             else os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    if not value:
        raise SystemExit("no credential: --account EMAIL, ANTHROPIC_API_KEY or --key-file")
    return value, "claude_api"


# ── one cell of the grid ──────────────────────────────────────────────────────────────────────

def request_for(candidate: Candidate, posting: Posting, template: str | None, kind: str = "cv",
                look: str = templates.DEFAULT):
    """What production would send for this person and posting, in the look everyone has by default or in
    `look`."""
    if template and COMPANY_SLOT in template:
        # A wording that reads what is known about the company (`company/<slug>.txt`), or that nothing is:
        # filled here, since production's slots (`build.prepare`) do not have it.
        template = template.replace(
            COMPANY_SLOT, posting.company_facts or "Nothing is known about the company beyond the posting.")
    return build.prepare(kind, user=candidate.user, job=posting, documents=candidate.documents,
                         look=templates.find(kind, look), languages=candidate.languages, template=template)


def write_one(client, candidate: Candidate, posting: Posting, *, template: str, model: str,
              effort: str = build.EFFORT, kind: str = "cv",
              look: str = templates.DEFAULT) -> tuple[str, dict]:
    """One document, through the production call: its whole page, and what writing it took (the seconds
    the call ran, and its tokens in and out where the account's tool counts them)."""
    request = request_for(candidate, posting, template, kind, look)
    started = time.monotonic()
    reply, body = build.draft(client, request, model=model, effort=effort)
    used = getattr(reply, "usage", None)
    return build.assemble(request, body), {
        "seconds": round(time.monotonic() - started, 1),
        "tokens_in": used.input_tokens if used else None, "tokens_out": used.output_tokens if used else None,
        # The model the provider says wrote it, which need not be the one asked for.
        "model": getattr(reply, "model", None)}


def kept_in(out: pathlib.Path, model: str, effort: str, wording: str) -> pathlib.Path:
    """Where the documents one model wrote from one wording are kept (owner, 2026-11-15: "save them
    based on the model that produced it and the version of the prompt that was used", a before and an
    after): `<out>/<model>/<version>/`, the wording itself beside them as `prompt.md`. The version is the
    one production keeps with a document (`prompts.version`), so the live prompt's folder is the stamp on
    people's own documents. A model's folder says how hard it was told to think, where it was told: a
    Claude model always is, Codex only by `--codex-effort`."""
    name = model or "the-accounts-own-model"
    told = effort if name.startswith("claude") else cli.CODEX_EFFORT
    folder = out / (f"{name}@{told}" if told else name) / version_of(wording)
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "prompt.md").exists():
        (folder / "prompt.md").write_text(wording, encoding="utf-8")
    return folder


def version_of(wording: str) -> str:
    return hashlib.sha256(wording.encode("utf-8")).hexdigest()[:12]


def over_ceiling(tokens_out: int | None) -> int | None:
    """Whether a document took more tokens out, thinking included, than an API key is given
    (`build.MAX_TOKENS`). A sign-in's tool sets its own ceiling, so the bench writes such a document whole;
    on a key the same call is cut off and the person gets none. A wording that makes the model think
    longer shows here first: 3 of 18 CVs of one candidate wording, none of the live one's (2026-11-15)."""
    return None if tokens_out is None else int(tokens_out > build.MAX_TOKENS)


# The bench writes cells side by side and the worker's one Chrome takes one document at a time over one
# pipe: two renders at once crossed their answers ("KeyError: 'targetId'") once no model call spaced
# them out (a --dry-run, 2026-11-11).
_RENDERING = threading.Lock()


def rendered(page: str, label: str, kind: str = "cv") -> tuple[bytes | None, pdf.PdfReport]:
    """The PDF a person would download and what an ATS reads of it, as the docgen worker makes it
    (`pdf.fitted`); no PDF without Chrome (`CHROME_BIN`)."""
    try:
        with _RENDERING:
            return pdf.fitted(page, kind=kind, headings=build.headings(body_of(page)) if kind == "cv" else ())
    except pdf.CompileFailed as exc:
        logger.warning("no PDF for %s, so no page count: %s", label, exc)
        return None, pdf.PdfReport(pages=None, chars=0, issues=["not rendered"])


def body_of(page: str) -> str:
    """What the model wrote, out of the whole page: after the header, and after a letter's company block."""
    body = page.split('<hr class="header-rule">', 1)[-1].split("</body>", 1)[0]
    return re.sub(r'(?s)^\s*<div class="letter-meta">.*?</div>', "", body)


def measured(candidate: Candidate, posting: Posting, page: str, document: bytes | None, *,
             report: pdf.PdfReport, kind: str = "cv") -> dict:
    """What code says of a written document, as the docgen worker checks it (`build.problems`): its PDF's
    pages, how much smaller the look was drawn to take up a few spilled lines, what an ATS reads, the
    numbers found nowhere in their documents or the posting, and what a reviewer would send back. For a
    CV, then the whole ATS checklist (`ats_read`)."""
    body = body_of(page)
    request = request_for(candidate, posting, None, kind)
    check = build.problems(request, body, report.pages, report.spilled)
    row = {"pages": report.pages, "scale": report.scale, "fitted": int(report.scale < 1),
           "ats_issues": len(report.issues), "invented": len(check.invented),
           "invented_numbers": list(check.invented), "faults": len(check.faults),
           "fault_list": list(check.faults), "revision_needed": not check.ok}
    if kind == "cv":
        issues = ats_read(document, page) if document else ["not rendered"]
        row |= {"ats_read": len(issues), "ats_read_issues": issues}
    if document:
        passed = (ats_checks(document, page, posting.title, report, row["ats_read_issues"]) if kind == "cv"
                  else ats_letter_checks(document, page, posting, report))
        row |= {"ats_score": round(100 * sum(passed.values()) / len(passed), 1),
                "ats_failed": [name for name, ok in passed.items() if not ok],
                **{f"ats_{name}": int(ok) for name, ok in passed.items()}}
    return row


def letter_text(page: str) -> str:
    """A letter as a person pastes it: greeting to name, a blank line between paragraphs and none between
    the sign-off and the name. The subject line stays out, as it does from a letter box."""
    found = re.search(r'(?s)<div class="letter">(.*)</div>', page)
    blocks: list[str] = []
    letter = found.group(1) if found else body_of(page)
    for attributes, inner in re.findall(r"(?s)<p([^>]*)>(.*?)</p>", letter):
        text = " ".join(unescape(re.sub(r"<[^>]+>", " ", inner)).split())
        if "letter-subject" in attributes or not text:
            continue
        if "signature" in attributes and blocks:
            blocks[-1] += f"\n{text}"
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def entries_of(page: str) -> tuple[tuple[str, str, str], ...]:
    """Each job and qualification a written CV lists: what it is, where, and its dates as written."""
    found = []
    for entry in re.findall(r'(?s)<div class="entry">(.*?)(?=<div class="entry">|$)', page):
        title = re.search(r'(?s)<span class="entry-title">(.*?)</span>', entry)
        dates = re.search(r'(?s)<span class="entry-date">(.*?)</span>', entry)
        parts = [" ".join(unescape(re.sub(r"<[^>]+>", " ", part)).split())
                 for part in (title.group(1) if title else "").split("·")]
        if len(parts) >= 2:
            found.append((parts[0], parts[1], unescape(re.sub(r"<[^>]+>", " ", dates.group(1))).strip()
                          if dates else ""))
    return tuple(found)


def contact_of(page: str) -> tuple[str, str]:
    """The email and phone number the header gives, as written: what a reader should read back."""
    line = re.search(r'(?s)<div class="cv-contact">(.*?)</div>', page)
    items = [" ".join(unescape(re.sub(r"<[^>]+>", " ", each)).split())
             for each in re.findall(r"(?s)<span>(.*?)</span>", line.group(1) if line else "")]
    return (next((each for each in items if "@" in each), ""),
            next((each for each in items if re.fullmatch(r"\+?[\d\s().-]{7,}", each)), ""))


def ats_read(document: bytes, page: str) -> list[str]:
    """The ATS checklist every look is held to (`libs/templates/README.md`, "Adding a look"), on a CV a
    model wrote: read by pypdf in the order Chrome wrote it and by pdfminer by where each word sits, it
    starts with the name, every word comes out whole with no ligature, and the headings come in order."""
    from pdfminer.high_level import extract_text  # noqa: PLC0415 - a development dependency

    def words(text: str) -> set[str]:
        return set(re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold()))

    written = words(unescape(re.sub(r"<[^>]+>", " ", page.split("<body>", 1)[1])))
    name = unescape(re.search(r'class="cv-name">([^<]*)<', page).group(1)).strip()
    heads = [unescape(each).casefold() for each in re.findall(r'class="section-title">([^<]*)<', page)]
    issues = []
    for reader, text in (("pypdf", "\n".join(each.extract_text() or "" for each in
                                              PdfReader(io.BytesIO(document)).pages)),
                         ("pdfminer", extract_text(io.BytesIO(document)))):
        if re.search("[\ufb00-\ufb06]", text):
            issues.append(f"{reader}: a ligature")
        # Ledger and Foundry draw the name in capitals, which is the name all the same.
        if name and not text.strip().casefold().startswith(name.casefold()):
            issues.append(f"{reader}: does not start with the name")
        if (read := words(text)) != written:
            issues.append(f"{reader}: words not read whole {sorted(read ^ written)[:6]}")
        flat, at = " ".join(text.split()).casefold(), 0
        for heading in heads:
            found = flat.find(" ".join(heading.split()), at)
            if found < 0:
                issues.append(f"{reader}: heading {heading!r} out of place")
            at = max(at, found + 1)
    return issues


# A job's dates as an ATS takes them: "Mar 2021 – Present", "2016 – 2019", "Jun 2026", "2024".
_ATS_MONTH_YEAR = r"(?:[A-Z][a-z]{2,8}\.? )?\d{4}"
_ATS_DATE = re.compile(rf"{_ATS_MONTH_YEAR}(?: [\u2013-] (?:{_ATS_MONTH_YEAR}|[Pp]resent))?")


def ats_letter_checks(document: bytes, page: str, posting: Posting, report: pdf.PdfReport) -> dict[str, bool]:
    """What an applicant tracking system, and the person who reads after it, have to be able to take from a
    cover letter, each a yes or a no, none depending on what the person has done (owner, 2026-11-16: letters
    "should be put through the ats procedure as well"):

    - `text`: every word comes out whole for both readers, with no ligature;
    - `name`: the text starts with the person's name;
    - `contact`: the email address and the phone number come out as written;
    - `company`: the employer is named in the letter;
    - `title`: the posting's job title is in it, in the subject or the letter, as a reader searches by it;
    - `length`: the letter itself is `checks.LETTER_WORDS` long, the length a hiring manager reads;
    - `pages`: one page."""
    from pdfminer.high_level import extract_text  # noqa: PLC0415 - a development dependency

    pages = PdfReader(io.BytesIO(document)).pages
    readers = {"pypdf": "\n".join(each.extract_text() or "" for each in pages),
               "pdfminer": extract_text(io.BytesIO(document))}
    written = set(re.findall(r"\w+", unicodedata.normalize("NFC", unescape(
        re.sub(r"<[^>]+>", " ", page.split("<body>", 1)[1]))).casefold()))
    named = re.search(r'class="cv-name">([^<]*)<', page)
    name = unescape(named.group(1)).strip() if named else ""
    read = " ".join(readers["pypdf"].split()).casefold()
    email, phone = contact_of(page)

    def words(text: str) -> str:
        return " ".join(each for each in re.findall(r"\w+", text.casefold()) if each != "and")

    def digits(text: str) -> str:
        return re.sub(r"\D", "", text)

    company = re.sub(r"(?i)\b(ltd|limited|plc|gmbh|kft|inc|llc)\b\.?", "", posting.company or "")
    letter_words = len(letter_text(page).split())
    return {
        "text": all(not re.search("[\ufb00-\ufb06]", text)
                    and set(re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold())) == written
                    for text in readers.values()),
        "name": bool(name) and all(" ".join(text.split()).casefold().startswith(name.casefold())
                                   for text in readers.values()),
        "contact": email.casefold() in read and digits(phone) in digits(read),
        "company": bool(words(company)) and f" {words(company)} " in f" {words(read)} ",
        "title": f" {words(checkers.job_title(posting.title))} " in f" {words(read)} ",
        "length": build.checks.LETTER_WORDS[0] <= letter_words <= build.checks.LETTER_WORDS[1],
        "pages": report.pages == 1,
    }


def ats_checks(document: bytes, page: str, title: str, report: pdf.PdfReport,
               issues: list[str]) -> dict[str, bool]:
    """What an applicant tracking system has to be able to do with a CV, each a yes or a no (owner,
    2026-11-15: a CV passes its ATS checks "perfectly at least", so that a person sees it). None of them
    depends on what the person has done: they are the writer's and the look's to get right.

    - `text`: every word comes out whole for both readers, with no ligature, and the worker's own read of
      the PDF finds nothing wrong with it (text there at all, lines of a sentence's length);
    - `name`: the text starts with the person's name;
    - `headings`: the section headings come out in their order;
    - `sections`: an Experience and an Education section, under those words;
    - `contact`: the email address and the phone number come out as written;
    - `dates`: every job's and course's dates are in the one form a parser takes;
    - `title`: the posting's job title is in the CV, as recruiters search by it and scanners look for it;
    - `pages`: one or two pages, and nothing stranded on a last one.
    `issues` is what `ats_read` found."""
    read = " ".join("\n".join(each.extract_text() or "" for each in PdfReader(io.BytesIO(document)).pages)
                    .split()).casefold()
    email, phone = contact_of(page)
    heads = unescape(" | ".join(re.findall(r'class="section-title">([^<]*)<', page))).casefold()
    def words(text: str) -> str:
        """Its words alone, in order: "Retail & Sales Advisor" is "Retail and Sales Advisor"."""
        return " ".join(each for each in re.findall(r"\w+", text.casefold()) if each != "and")

    wanted = words(checkers.job_title(title))

    def digits(text: str) -> str:
        return re.sub(r"\D", "", text)

    return {
        "text": not report.issues and not any("ligature" in each or "not read whole" in each
                                              for each in issues),
        "name": not any("start with the name" in each for each in issues),
        "headings": not any("out of place" in each for each in issues),
        "sections": "experience" in heads and "education" in heads,
        "contact": email.casefold() in read and digits(phone) in digits(read),
        "dates": all(_ATS_DATE.fullmatch(dates) for _, _, dates in entries_of(page) if dates),
        "title": bool(wanted) and f" {wanted} " in f" {words(read)} ",
        "pages": report.pages in (1, 2) and not report.spilled,
    }


def judge_one(client, posting: Posting, text: str, report: dict, theirs: str, *,
              kind: str = "cv") -> dict | None:
    """The reviewer's verdict, on what no regular expression reaches. `theirs` is everything the person
    gave us: a reviewer who cannot see it cannot say what the document made up. A letter's reviewer is shown
    what is known about the company too, for the same reason."""
    letter = kind == "cover"
    prompt = ((LETTER_JUDGE_PROMPT if letter else JUDGE_PROMPT).read_text(encoding="utf-8")
              .replace("<DETERMINISTIC_SUMMARY>", json.dumps(report))
              .replace("<JOB_DESCRIPTION>", f"{posting.title}\n{posting.company}\n\n{posting.description}")
              .replace("<COMPANY_FACTS>", posting.company_facts or "Nothing beyond the posting.")
              .replace("<THEIR_DOCUMENTS>", theirs)
              .replace("<CV_TEXT>", text))
    try:
        system = LETTER_JUDGE_SYSTEM if letter else JUDGE_SYSTEM
        reply = client.complete_text(model=JUDGE_MODEL, system=system,
                                     messages=[{"role": "user", "content": prompt}],
                                     max_tokens=JUDGE_MAX_TOKENS)
        verdict = read_verdict(reply, text, LETTER_DIMENSIONS if letter else DIMENSIONS)
        if verdict is None:
            logger.warning("judge returned no JSON object for %s (%d chars)", posting.slug, len(reply))
        return verdict
    except Exception as exc:                                    # noqa: BLE001 - a lab, not a lane
        logger.warning("judge failed on %s, scoring without it: %s", posting.slug, exc)
        return None


def _flat(text: str) -> str:
    """Text as it is compared: one space between words, and quotes and dashes as typed on a keyboard."""
    return " ".join(str(text).translate(_TYPED).split())


_TYPED = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                        "\u2014": "-"})


def read_verdict(reply: str, text: str, dimensions: tuple[str, ...] | None = None) -> dict | None:
    """The reviewer's answer, kept only where code can check it (rules before AI). A mark whose quoted line
    is not in the CV word for word is thrown away, as the prompt says it will be, and so is a line said not
    to be theirs that the CV does not have. The overall mark is the mean of the marks kept, worked out
    here: asked for as a number of its own, it moved between two reads of one CV."""
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end == -1:
        return None
    answer, cv = json.loads(reply[start:end + 1]), _flat(text)

    def quoted(line: object) -> bool:
        return bool(line) and _flat(line) in cv

    marks = {name: mark for name, mark in (answer.get("dimensions") or {}).items()
             if name in (dimensions or DIMENSIONS) and isinstance(mark, dict)
             and isinstance(mark.get("score"), int | float)}
    kept = {name: mark for name, mark in marks.items() if quoted(mark.get("evidence"))}
    scores = [mark["score"] for mark in kept.values()]
    return {"overall_100": round(10 * statistics.mean(scores)) if scores else None,
            "verdict": answer.get("would_reply"), "one_line": answer.get("one_line"), "dimensions": kept,
            "unquoted": len(marks) - len(kept),
            "not_theirs": [each for each in answer.get("not_theirs") or []
                           if isinstance(each, dict) and quoted(each.get("line"))],
            "left_out": answer.get("left_out") or [], "top_fixes": answer.get("top_fixes") or []}


def _rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return "rate_limit" in text or "429" in text


def _verdict(client, posting: Posting, text: str, report: dict, *, kept: pathlib.Path,
             judge: bool, force: bool, theirs: str = "", kind: str = "cv") -> dict | None:
    """This CV's verdict, from beside it on disk when it has already been judged.

    **A verdict is cached with its CV.** Without this, every resume re-judged each finished cell
    before it would write a new one, so an interrupted grid spent its whole next run re-buying
    judgements it already had and made no progress at all. The CV is the thing that varies between
    variants; a second opinion on a byte-identical document is not worth a Sonnet call.
    """
    if not judge:
        return None
    if kept.exists() and not force:
        return json.loads(kept.read_text(encoding="utf-8"))
    if client is None:
        return None
    verdict = judge_one(client, posting, text, report, theirs, kind=kind)
    if verdict is not None:
        kept.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    return verdict


class _LazyClient:
    """The client, created the first time something actually calls it: on `--account`, the AI account
    that person has in use in the local database, else an API key."""

    def __init__(self, key_file: str | None, account: str | None, provider: str | None = None):
        self._key_file, self._account, self._provider = key_file, account, provider
        self._client = None
        self._held = None
        self._lock = threading.Lock()

    def __getattr__(self, name: str):
        with self._lock:
            if self._client is None:
                self._client = self._signed_in() if self._account else client_for(*credential(self._key_file))
        return getattr(self._client, name)

    def _signed_in(self):
        from libs.db import database  # noqa: PLC0415 - only `--account` needs one
        from libs.llm import accounts  # noqa: PLC0415
        from libs.llm.providers import paying_client  # noqa: PLC0415
        from libs.secrets import SecretStore  # noqa: PLC0415

        db = database()
        row = db.users.find_one({"email": self._account})
        if row and self._provider:
            # The one they linked for that provider, in use or not: the newest.
            account = next((each for each in accounts.listing(db, row["_id"])
                            if accounts.PROVIDER[each.kind] == self._provider), None)
        else:
            account = accounts.in_use(db, row["_id"]) if row else None
        if account is None:
            raise SystemExit(f"{self._account} has no {self._provider or 'AI'} account "
                             f"{'linked' if self._provider else 'in use'} in {os.environ.get('MONGODB_DB')}")
        # Held for the whole run: the secret stays redacted from every log line while it is.
        self._held = paying_client(SecretStore(db), row["_id"], str(account.id))
        paid = self._held.__enter__()
        if paid is None:
            raise SystemExit("that AI account could not be opened")
        logger.info("credential: %s", account.kind)
        return paid[0]


def _run_cell(client, candidate: Candidate, posting: Posting, *, variant: str, sample: int,
              template: str | None, out: pathlib.Path, args, judge=None) -> dict | None:
    """One document and its verdicts, generating whichever part is not already on disk. `judge` reads it
    where the account that writes cannot (a ChatGPT or Gemini sign-in asked for a Claude reviewer)."""
    kind, uploaded = args.kind, variant == UPLOADED
    tag = "" if kind == "cv" else ".cover"
    wording = None if uploaded else template if template is not None else load_variant(variant, kind)
    version = version_of(wording) if wording else None
    # Asked for no model (a ChatGPT sign-in takes its plan's own), it is kept under its provider's name.
    out = out / UPLOADED if uploaded else kept_in(
        out, args.model or getattr(args, "provider", None) or "", args.effort, wording)
    out.mkdir(parents=True, exist_ok=True)
    # A wording written for another look than the default is a variant of its own: "live@halcyon".
    look = getattr(args, "look", None) or templates.DEFAULT
    if look != templates.DEFAULT and not uploaded:
        variant = f"{variant}@{look}"
    path = out / f"{candidate.name}__{variant}__{posting.slug}__{sample}{tag}.{'txt' if uploaded else 'html'}"
    # What writing it took, kept beside it: a later run measures the same document for nothing.
    cost = path.with_suffix(".cost.json")

    if uploaded:
        # What they would have sent: their newest CV or letter, as they wrote it.
        own = candidate.documents.get(BASE[kind])
        if not own:
            logger.warning("%s uploaded no %s, so there is no %s baseline", candidate.name, BASE[kind],
                           UPLOADED)
            return None
        page = own[0]
        path.write_text(page, encoding="utf-8")
    elif args.dry_run:
        if not path.exists():
            return None
        page = path.read_text(encoding="utf-8")
    elif path.exists() and not args.force:
        # A document already written is a document already paid for.
        page = path.read_text(encoding="utf-8")
    else:
        page, took = _with_backoff(
            lambda: write_one(client, candidate, posting, template=template, model=args.model,
                              effort=args.effort, kind=kind, look=look),
            what=f"{candidate.name}/{variant}/{posting.slug}#{sample}")
        if took["model"] and args.model and took["model"] != args.model:
            logger.warning("%s was asked for and %s answered: kept under the one asked for, named in its "
                           "record", args.model, took["model"])
        # `answered` is what the provider named, None where it names none; `model` is that, else the
        # one asked for.
        took |= {"answered": took["model"], "model": took["model"] or args.model or None,
                 "asked": args.model, "effort": args.effort, "codex_effort": cli.CODEX_EFFORT,
                 "wording": variant, "prompt_version": version}
        path.write_text(page, encoding="utf-8")
        cost.write_text(json.dumps(took), encoding="utf-8")
    took = json.loads(cost.read_text(encoding="utf-8")) if cost.exists() and not uploaded else {}
    seconds = took.get("seconds") or 0.0

    document, read = (None, None) if uploaded else rendered(page, posting.slug, kind)
    report = {} if uploaded else {**(cvchecks.check(page) if kind == "cv" else {}),
                                  **measured(candidate, posting, page, document, report=read, kind=kind)}
    text = page if uploaded else cvchecks.text_of(page) if kind == "cv" else letter_text(page)
    theirs = "\n\n".join(each for texts in candidate.documents.values() for each in texts)
    verdict = _verdict(judge or client, posting, text, report, kept=path.with_suffix(".verdict.json"),
                       judge=args.judge, force=args.force, theirs=theirs, kind=kind)
    dimensions = {key: value.get("score")
                  for key, value in ((verdict or {}).get("dimensions") or {}).items()}
    outside = {}
    if args.checkers:
        email, phone = ("", "") if uploaded else contact_of(page)
        # Their own document is the same for every sample, so it is asked about once, as sample 1.
        asked = path.with_name(path.name.replace(f"__{sample}{tag}.", f"__1{tag}.")) if uploaded else path
        outside = checkers.measure(args.checkers, checkers.Document(
            kind=kind, path=asked, text=text, pdf=document, title=posting.title, posting=posting.description,
            theirs=theirs, email=email, phone=phone, name=candidate.user.full_name or "",
            entries=() if uploaded or kind != "cv" else entries_of(page)))
    row = {"candidate": candidate.name, "variant": variant, "job": posting.slug, "kind": kind,
           "group": posting.group, "sample": sample, "look": None if uploaded else look,
           "model": took.get("model"), "prompt_version": version,
           "seconds": round(seconds, 1), "tokens_in": took.get("tokens_in"),
           "tokens_out": took.get("tokens_out"), "over_ceiling": over_ceiling(took.get("tokens_out")),
           **report, **outside,
           "violations": cvchecks.violations(report) if report and kind == "cv" else None,
           "overall_100": (verdict or {}).get("overall_100"),
           "verdict": (verdict or {}).get("verdict"),
           "one_line": (verdict or {}).get("one_line"),
           # What the reviewer found in the CV that is not in their documents, and what it left unused.
           "judge_not_theirs": len(verdict["not_theirs"]) if verdict and "not_theirs" in verdict else None,
           "judge_left_out": len(verdict["left_out"]) if verdict and "left_out" in verdict else None,
           "dimensions": dimensions,
           "top_fixes": (verdict or {}).get("top_fixes") or []}
    logger.info("%-7s %-10s %-42s #%d  %4.1fs pages=%s invented=%s judge=%s", candidate.name, variant,
                posting.slug[:42], sample, seconds, report.get("pages"), report.get("invented"),
                row["overall_100"])
    return row


# A 429 is not a failure of the grid, it is a request to slow down. Aborting on the first one threw
# away a run that would have finished; retrying forever hides a limit that will not clear. Bounded,
# with the wait growing, and the cell is skipped rather than the whole run abandoned.
RATE_LIMIT_WAITS = (30, 90, 240)


def _with_backoff(call, *, what: str):
    """Run one paid call, waiting out a rate limit rather than losing the grid to it."""
    for attempt, wait in enumerate((*RATE_LIMIT_WAITS, None), start=1):
        try:
            return call()
        except Exception as exc:
            if not _rate_limited(exc) or wait is None:
                raise
            logger.warning("rate limited on %s, waiting %ds (attempt %d)", what, wait, attempt)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def at_once(args) -> int:
    """How many cells run together. One on a person's linked account, as the docgen worker writes: it is
    most often a sign-in, whose tool renews the session when it starts, and two starting together can each
    renew it, the loser retiring the sign-in for everybody (2026-11-14: one was lost to a renewal that was
    not saved). A dry run writes nothing, but with `--judge` it still calls the reviewer."""
    calls = not args.dry_run or getattr(args, "judge", False)
    return 1 if args.account and calls else max(args.concurrency, 1)


def run(args) -> int:
    jobs_root = pathlib.Path(args.jobs_dir)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    candidates = {candidate.name: candidate for candidate in
                  (Candidate(spec, pathlib.Path(args.candidates_dir)) for spec in args.candidates.split(","))}
    variants = args.variants.split(",")

    # Built on first use, never on start-up. `--dry-run --judge` over a grid whose verdicts are
    # all cached makes no call at all, and the docstring promises that costs nothing — it was
    # refusing to start without a credential it would never have used.
    provider = getattr(args, "provider", None)
    client = _LazyClient(args.key_file, args.account, provider)
    # The reviewer is Claude's whoever writes: on the account in use.
    judge = _LazyClient(args.key_file, args.account) if provider else client

    # Every cell up front, so the work can be spread across threads instead of across processes.
    # Four processes was the previous shape and it cost a merge step, four log files, and a rate
    # limit nobody could see coming because no one process knew what the others were spending.
    cells = []
    for name, candidate in candidates.items():
        postings = load_postings(jobs_root, name, args.only.split(",") if args.only else None)
        for variant in variants:
            template = None if args.dry_run else load_variant(variant, args.kind)
            if variant != UPLOADED and template is None and not args.dry_run:
                raise SystemExit(f"variant {variant} has no prompt")
            for posting in postings:
                for sample in range(1, args.samples + 1):
                    cells.append((candidate, posting, variant, sample, template))

    logger.info("%d cell(s), %d at a time", len(cells), at_once(args))
    rows: list[dict] = []
    failed = 0
    with futures.ThreadPoolExecutor(max_workers=at_once(args)) as pool, \
            contextlib.closing(checkers):
        pending = {pool.submit(_run_cell, client, candidate, posting, variant=variant, sample=sample,
                               template=template, out=out, args=args, judge=judge): (
                                   candidate.name, variant, posting.slug, sample)
                   for candidate, posting, variant, sample, template in cells}
        for done in futures.as_completed(pending):
            try:
                row = done.result()
            except Exception as exc:                            # noqa: BLE001 - a lab, not a lane
                failed += 1
                logger.error("%s failed: %s", "/".join(str(part) for part in pending[done]), exc)
                continue
            if row is not None:
                rows.append(row)

    if failed:
        # Never silent: a grid that measured 60 of 72 cells and printed a scoreboard as though it
        # measured all of them is how a prompt gets adopted on a partial comparison.
        logger.warning("%d of %d cell(s) failed — re-run the same command to fill them in",
                       failed, len(cells))
    look = getattr(args, "look", None)
    baseline = args.baseline if look in (None, templates.DEFAULT) else f"{args.baseline}@{look}"
    return _finish(rows, out, baseline, args.kind)


def _finish(rows: list[dict], out: pathlib.Path, baseline: str = "live", kind: str = "cv") -> int:
    if not rows:
        logger.warning("nothing was measured, so nothing was written")
        return 1
    # A letter's grid may share a folder with a CV's, so neither writes over the other's numbers.
    tag = "" if kind == "cv" else ".cover"
    (out / f"results{tag}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    board = scoreboard(rows, baseline)
    print(board)
    (out / f"scoreboard{tag}.md").write_text(board, encoding="utf-8")
    return 0


# ── the numbers ───────────────────────────────────────────────────────────────────────────────

MECHANICAL = ("first_person", "profile_first_person", "third_person", "profile_third_person",
              "present_in_ended", "past_in_held",
              "task_bullets", "said_twice",
              "colon_bullets", "emdash_violations",
              "cliches", "profile_words", "entries", "bullets", "est_pages", "pages", "fitted", "ats_issues",
              "ats_score", "ats_title", "ats_dates",
              "faults", "invented", "seconds", "tokens_in", "tokens_out", "over_ceiling")

# The per-variant table, a column for each of ours that any row has: a letter has no bullets to count.
COLUMNS = (("1st person", "first_person"), ("profile 1p", "profile_first_person"),
           ("3rd-person bullets", "third_person"), ("profile in 3rd person", "profile_third_person"),
           ("present in an ended job", "present_in_ended"),
           ("past in a held job", "past_in_held"), ("task bullets", "task_bullets"),
           ("said twice in the same words", "said_twice"),
           ("colon bullets", "colon_bullets"), ("em-dash", "emdash_violations"), ("clichés", "cliches"),
           ("profile words", "profile_words"), ("entries", "entries"), ("pages", "pages"),
           ("lines taken up", "fitted"), ("ATS checks passed, of 100", "ats_score"),
           ("job title in it", "ats_title"), ("dates in one form", "ats_dates"),
           ("sent back for", "faults"), ("not theirs", "invented"),
           ("seconds", "seconds"), ("tokens in", "tokens_in"), ("tokens out", "tokens_out"),
           ("over a key's ceiling", "over_ceiling"))

LETTER_DIMENSIONS = ("opening", "why_here", "evidence", "fit", "voice", "honesty", "close")
DIMENSIONS = ("six_second_pull", "role_angle_fit", "relevance_coverage", "achievement_quality",
              "human_voice", "defensibility", "keyword_integration")
# What the reviewer lists beside its marks, as counts.
FOUND = ("judge_not_theirs", "judge_left_out")


def _value(row: dict, key: str):
    """One measure off one row, whether it is a column or one of the judge's dimensions.

    The seven dimensions arrive nested under `dimensions`, and reading them top-level returned
    `None` for every one — so the whole judge breakdown was silently absent from the scoreboard
    and only `overall_100` was ever compared. Exactly the failure §3a warns about: a comparison
    that reports what moved and cannot report what did not.
    """
    if key in row:
        return row[key]
    return (row.get("dimensions") or {}).get(key)


def _mean(values: list) -> float | None:
    clean = [v for v in values if v is not None]
    return statistics.mean(clean) if clean else None


def _variant_mean(rows: list[dict], variant: str, key: str) -> float | None:
    """One variant's mean for one measure, over the samples of one candidate+posting."""
    return _mean([_value(row, key) for row in rows if row["variant"] == variant])


def _means(rows: list[dict], variants: list[str], outside: list[str]) -> list[str]:
    """One person's means per variant: ours, a column for each measure any row has, then outside tools'."""
    ours = [(label, key) for label, key in COLUMNS if any(_value(row, key) is not None for row in rows)]
    lines = ["| variant | n | judge /100 | judged | " + " | ".join(label for label, _ in ours) + " |",
             "|---|---|---|---|" + "---|" * len(ours)]
    theirs = [f"| variant | n | {' | '.join(outside)} |", "|---|---|" + "---|" * len(outside)]
    for variant in variants:
        group = [row for row in rows if row["variant"] == variant]
        if not group:
            continue
        scored = [row["overall_100"] for row in group if row["overall_100"] is not None]
        judged = f"{statistics.mean(scored):.1f}" if scored else "n/a"

        def avg(key: str, rows_in: list[dict] = group) -> str:
            value = _mean([_value(row, key) for row in rows_in])
            return "n/a" if value is None else f"{value:.1f}"
        lines.append(f"| {variant} | {len(group)} | {judged} | {len(scored)}/{len(group)} | "
                     + " | ".join(avg(key) for _, key in ours) + " |")
        theirs.append(f"| {variant} | {len(group)} | " + " | ".join(avg(key) for key in outside) + " |")
    said = ["What tools that are not ours said, as means:", "", *theirs, ""] if outside else []
    return [*lines, "", *said]


def scoreboard(rows: list[dict], baseline: str = "live") -> str:
    """Per-variant means, then the paired comparison, then what did not move."""
    variants = list(dict.fromkeys(row["variant"] for row in rows))
    # The baseline goes first whenever it was run, so every difference reads "variant minus baseline"
    # no matter which arm's results.json was merged first. It was decided by glob order, which flipped
    # the sign of the whole comparison depending on a folder name.
    if baseline in variants:
        variants = [baseline] + [name for name in variants if name != baseline]
    candidates = list(dict.fromkeys(row["candidate"] for row in rows))
    lines: list[str] = []

    # What tools that are not ours said (`checkers.py`), each measure as `<checker>.<measure>`.
    outside = sorted({key for row in rows for key in row if "." in key})

    for name in candidates:
        mine = [row for row in rows if row["candidate"] == name]
        lines += [f"### {name}", "", *_means(mine, variants, outside)]

    # The comparison. Paired on (candidate, posting) so a hard posting cannot favour whichever
    # variant happened to draw it, and averaged within a group first so one role listed in a dozen
    # cities counts once rather than a dozen times.
    base = variants[0]
    for variant in variants[1:]:
        # **"higher / lower" and never "better / worse".** Half these measures improve by going
        # down — first person, colons, clichés — so a "better" column would have called the
        # third-person draft's whole point a regression.
        lines += [f"### {variant} vs {base}, paired by posting", "",
                  "| measure | mean difference | spread (sd) | std error | pairs higher / lower / same |",
                  "|---|---|---|---|---|"]
        for key in ("overall_100", *MECHANICAL, *DIMENSIONS, *FOUND, *outside):
            deltas = []
            for name in candidates:
                by_group: dict[str, list[float]] = {}
                for slug in {row["job"] for row in rows if row["candidate"] == name}:
                    cell = [row for row in rows if row["candidate"] == name and row["job"] == slug]
                    left, right = _variant_mean(cell, base, key), _variant_mean(cell, variant, key)
                    if left is None or right is None:
                        continue
                    by_group.setdefault(cell[0]["group"], []).append(right - left)
                deltas += [statistics.mean(values) for values in by_group.values()]
            if not deltas:
                continue
            better = sum(1 for d in deltas if d > 0.001)
            worse = sum(1 for d in deltas if d < -0.001)
            same = len(deltas) - better - worse
            # The standard error is what says whether a difference is distinguishable from run to
            # run variation at all. Without it a mean of -1.06 reads as a finding when the samples
            # it came from were 3.5 apart, and a prompt gets replaced on noise.
            if len(deltas) > 1:
                deviation = statistics.stdev(deltas)
                spread = f"{deviation:.2f}"
                error = f"±{deviation / math.sqrt(len(deltas)):.2f}"
            else:
                spread, error = "n/a", "n/a"
            lines.append(f"| {key} | {statistics.mean(deltas):+.2f} | {spread} | {error} "
                         f"| {better} / {worse} / {same} |")
        lines.append("")
        lines.append(f"{len(candidates)} candidate(s), paired on posting groups. A mean "
                     "difference smaller than about twice its standard error did not move: that "
                     "is a result, and it belongs in the CHANGELOG entry with the ones that did.")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kind", choices=tuple(build.PROMPTS), default="cv",
                        help="what is written and measured: a CV, or a cover letter")
    parser.add_argument("--variants", default="live",
                        help="comma-separated; `live` is the deployed prompt, `uploaded` their own document")
    parser.add_argument("--baseline", default="live", help="the variant every other is measured against")
    parser.add_argument("--candidates", default="andras,ruben",
                        help="comma-separated: a folder under --candidates-dir, or NAME=db:EMAIL")
    parser.add_argument("--account", default=None,
                        help="write and judge on the AI account this person has in use in the local database")
    parser.add_argument("--candidates-dir", default="scripts/cvlab/candidates")
    parser.add_argument("--jobs-dir", default="scripts/cvlab/jobs")
    parser.add_argument("--only", default="", help="comma-separated fixture slugs; default all")
    parser.add_argument("--samples", type=int, default=3,
                        help="generations per cell; one is not a result")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="cells in flight at once. Higher finishes sooner and trips the "
                             "account's rate limit sooner; a 429 is waited out, not fatal. Always one "
                             "with --account")
    parser.add_argument("--provider", default=None, choices=sorted(DEFAULT_DOCUMENT_MODELS),
                        help="with --account: write on the account they linked for this provider (codex is "
                             "a ChatGPT sign-in) instead of the one in use, with the model it writes "
                             "documents with; the reviewer stays on the account in use")
    parser.add_argument("--model", default=None, help=f"default {CV_MODEL}, or the provider's own")
    parser.add_argument("--effort", default=build.EFFORT, help="how hard the model thinks (low … max)")
    parser.add_argument("--research", action="store_true",
                        help="let a Claude sign-in search the web while it writes (production never does)")
    parser.add_argument("--codex-effort", default=None, choices=["minimal", "low", "medium", "high"],
                        help="with --provider codex: tell Codex how hard to think; production tells it "
                             "nothing, and the documents are kept apart under <model>@<effort>")
    parser.add_argument("--look", default=None, choices=[each.id for each in templates.of_kind("cv")],
                        help="the look a CV is written for and drawn in; its name joins the variant's")
    parser.add_argument("--judge", action="store_true", help="also run the recruiter verdict (CVs)")
    parser.add_argument("--checkers", default="",
                        help=f"comma-separated tools that are not ours, of {', '.join(checkers.CHECKERS)}; "
                             "each answers for its own kind of document")
    parser.add_argument("--dry-run", action="store_true",
                        help="score CVs already in --out, no API calls")
    parser.add_argument("--force", action="store_true", help="rewrite CVs already on disk")
    parser.add_argument("--key-file", default=None)
    parser.add_argument("--out", default="scripts/cvlab/out")
    args = parser.parse_args()
    cli.CODEX_EFFORT = args.codex_effort
    cli.WEB_SEARCH = args.research
    if args.model is None:
        args.model = DEFAULT_DOCUMENT_MODELS[args.provider] if args.provider else CV_MODEL
    args.checkers = [each for each in args.checkers.split(",") if each]
    if unknown := set(args.checkers) - set(checkers.CHECKERS):
        parser.error(f"no such checker: {', '.join(sorted(unknown))}")
    if idle := [name for name in args.checkers if checkers.CHECKERS[name][1] != args.kind]:
        parser.error(f"{', '.join(idle)} cannot read a {args.kind}")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # One line a request would bury the bench's own lines (CLAUDE.md §8).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
