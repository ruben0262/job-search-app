"""The CV lab: run writing-prompt variants over the same postings and compare the numbers.

Nothing here writes to a prompt. Variant `live` reads `libs/prompts/write_cv.md` at run time, so the
baseline is always the prompt every account is written with and cannot drift from it; every other
variant is a separate file under `variants/`, filled the same way (`build.prepare`). Variant `uploaded`
is the person's own CV as they uploaded it, with no call: what they would have sent without us.
`CLAUDE.md` §3a: the live prompt is measured, never edited, and a variant becomes the live prompt only
when the owner says so.

    # score CVs already on disk — no calls, no credential, no money
    python scripts/cvlab/run.py --dry-run --out scripts/cvlab/out/ab

    # the new prompt against the CV they uploaded, on the account they linked on this computer
    python scripts/cvlab/run.py --candidates ruben=db:someone@example.com --account someone@example.com \
        --variants uploaded,live --baseline uploaded --samples 3 --judge --out scripts/cvlab/out/first

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
from libs.llm import client_for  # noqa: E402
from libs.settings import CV_MODEL  # noqa: E402
from scripts.cvlab import cvchecks  # noqa: E402
from services.docgen import build, pdf  # noqa: E402

logger = logging.getLogger("cvlab")

HERE = pathlib.Path(__file__).resolve().parent
LIVE_PROMPT = REPO / "libs" / "prompts" / build.PROMPTS["cv"]
JUDGE_PROMPT = REPO / "libs" / "prompts" / "eval_cv.md"
# The person's own CV, unchanged: the baseline every written CV is measured against.
UPLOADED = "uploaded"

# Held fixed while the writing prompt varies: the judge is the instrument, not the thing measured.
JUDGE_MODEL = "claude-sonnet-5-5"

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


def load_variant(name: str) -> str | None:
    """`live` is the prompt every account is written with, read fresh; `uploaded` has none. Anything else
    is a file under `variants/`."""
    if name == UPLOADED:
        return None
    path = LIVE_PROMPT if name == "live" else HERE / "variants" / f"{name}.md"
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

def request_for(candidate: Candidate, posting: Posting, template: str | None):
    """What production would send for this person and posting, in the look everyone has by default."""
    return build.prepare("cv", user=candidate.user, job=posting, documents=candidate.documents,
                         look=templates.find("cv", templates.DEFAULT), languages=candidate.languages,
                         template=template)


def write_one(client, candidate: Candidate, posting: Posting, *, template: str,
              model: str, effort: str = build.EFFORT) -> tuple[str, float]:
    """One CV, through the production call: its whole page, and the seconds the call took."""
    request = request_for(candidate, posting, template)
    started = time.monotonic()
    _, body = build.draft(client, request, model=model, effort=effort)
    return build.assemble(request, body), time.monotonic() - started


# The bench writes cells side by side and the worker's one Chrome takes one document at a time over one
# pipe: two renders at once crossed their answers ("KeyError: 'targetId'") once no model call spaced
# them out (a --dry-run, 2026-11-11).
_RENDERING = threading.Lock()


def measured(candidate: Candidate, posting: Posting, page: str) -> dict:
    """What code says of a written CV, as the docgen worker checks it (`build.problems`): its PDF's pages and
    what an ATS reads, the numbers found nowhere in their documents or the posting, and what a reviewer
    would send back. Then the whole ATS checklist (`ats_read`). Pages are None without Chrome
    (`CHROME_BIN`)."""
    body = page.split('<hr class="header-rule">', 1)[-1].split("</body>", 1)[0]
    request = request_for(candidate, posting, None)
    try:
        with _RENDERING:
            document = pdf.render(page)
    except pdf.CompileFailed as exc:
        logger.warning("no PDF for %s, so no page count: %s", posting.slug, exc)
        document, report = None, pdf.PdfReport(pages=None, chars=0, issues=["not rendered"])
    else:
        report = pdf.inspect_pdf(document, headings=build.headings(body))
    check = build.problems(request, body, report.pages, report.last_page_chars)
    return {"pages": report.pages, "ats_issues": len(report.issues), "invented": len(check.invented),
            "invented_numbers": list(check.invented), "faults": len(check.faults),
            "fault_list": list(check.faults), "revision_needed": not check.ok,
            "ats_read": len(issues := ats_read(document, page) if document else ["not rendered"]),
            "ats_read_issues": issues}


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
        if name and not text.strip().startswith(name):
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


def judge_one(client, posting: Posting, text: str, report: dict) -> dict | None:
    """The recruiter's verdict, on what no regular expression reaches."""
    prompt = (JUDGE_PROMPT.read_text(encoding="utf-8")
              .replace("<DETERMINISTIC_SUMMARY>", json.dumps(report))
              .replace("<JOB_DESCRIPTION>", posting.description)
              .replace("<CV_TEXT>", text))
    try:
        reply = client.complete_text(model=JUDGE_MODEL,
                                     system="You are a strict technical recruiter.",
                                     messages=[{"role": "user", "content": prompt}],
                                     max_tokens=JUDGE_MAX_TOKENS)
        start, end = reply.find("["), reply.rfind("]")
        if start == -1 or end == -1:
            logger.warning("judge returned no JSON array for %s (%d chars)", posting.slug,
                           len(reply))
            return None
        return json.loads(reply[start:end + 1])[0]
    except Exception as exc:                                    # noqa: BLE001 - a lab, not a lane
        logger.warning("judge failed on %s, scoring without it: %s", posting.slug, exc)
        return None


def _rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return "rate_limit" in text or "429" in text


def _verdict(client, posting: Posting, text: str, report: dict, *, kept: pathlib.Path,
             judge: bool, force: bool) -> dict | None:
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
    verdict = judge_one(client, posting, text, report)
    if verdict is not None:
        kept.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    return verdict


class _LazyClient:
    """The client, created the first time something actually calls it: on `--account`, the AI account
    that person has in use in the local database, else an API key."""

    def __init__(self, key_file: str | None, account: str | None):
        self._key_file, self._account = key_file, account
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
        account = accounts.in_use(db, row["_id"]) if row else None
        if account is None:
            raise SystemExit(f"{self._account} has no AI account in use in {os.environ.get('MONGODB_DB')}")
        # Held for the whole run: the secret stays redacted from every log line while it is.
        self._held = paying_client(SecretStore(db), row["_id"], str(account.id))
        paid = self._held.__enter__()
        if paid is None:
            raise SystemExit("that AI account could not be opened")
        logger.info("credential: %s", account.kind)
        return paid[0]


def _run_cell(client, candidate: Candidate, posting: Posting, *, variant: str, sample: int,
              template: str | None, out: pathlib.Path, args) -> dict | None:
    """One CV and its verdict, generating whichever half is not already on disk."""
    uploaded = variant == UPLOADED
    path = out / f"{candidate.name}__{variant}__{posting.slug}__{sample}.{'txt' if uploaded else 'html'}"

    seconds = 0.0
    if uploaded:
        # What they would have sent: their newest CV, as they wrote it.
        page = candidate.documents["cv"][0]
        path.write_text(page, encoding="utf-8")
    elif args.dry_run:
        if not path.exists():
            return None
        page = path.read_text(encoding="utf-8")
    elif path.exists() and not args.force:
        # A CV already written is a CV already paid for.
        page = path.read_text(encoding="utf-8")
    else:
        page, seconds = _with_backoff(
            lambda: write_one(client, candidate, posting, template=template, model=args.model,
                              effort=args.effort),
            what=f"{candidate.name}/{variant}/{posting.slug}#{sample}")
        path.write_text(page, encoding="utf-8")

    report = {} if uploaded else {**cvchecks.check(page), **measured(candidate, posting, page)}
    text = page if uploaded else cvchecks.text_of(page)
    verdict = _verdict(client, posting, text, report, kept=path.with_suffix(".verdict.json"),
                       judge=args.judge, force=args.force)
    dimensions = {key: value.get("score")
                  for key, value in ((verdict or {}).get("dimensions") or {}).items()}
    row = {"candidate": candidate.name, "variant": variant, "job": posting.slug,
           "group": posting.group, "sample": sample, "seconds": round(seconds, 1), **report,
           "violations": cvchecks.violations(report) if report else None,
           "overall_100": (verdict or {}).get("overall_100"),
           "verdict": (verdict or {}).get("verdict"),
           "one_line": (verdict or {}).get("one_line"),
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
    client = _LazyClient(args.key_file, args.account)

    # Every cell up front, so the work can be spread across threads instead of across processes.
    # Four processes was the previous shape and it cost a merge step, four log files, and a rate
    # limit nobody could see coming because no one process knew what the others were spending.
    cells = []
    for name, candidate in candidates.items():
        postings = load_postings(jobs_root, name, args.only.split(",") if args.only else None)
        for variant in variants:
            template = None if args.dry_run else load_variant(variant)
            if variant != UPLOADED and template is None and not args.dry_run:
                raise SystemExit(f"variant {variant} has no prompt")
            for posting in postings:
                for sample in range(1, args.samples + 1):
                    cells.append((candidate, posting, variant, sample, template))

    logger.info("%d cell(s), %d at a time", len(cells), args.concurrency)
    rows: list[dict] = []
    failed = 0
    with futures.ThreadPoolExecutor(max_workers=max(args.concurrency, 1)) as pool:
        pending = {pool.submit(_run_cell, client, candidate, posting, variant=variant, sample=sample,
                               template=template, out=out, args=args): (candidate.name, variant,
                                                                        posting.slug, sample)
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
    return _finish(rows, out, args.baseline)


def _finish(rows: list[dict], out: pathlib.Path, baseline: str = "live") -> int:
    if not rows:
        logger.warning("nothing was measured, so nothing was written")
        return 1
    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    board = scoreboard(rows, baseline)
    print(board)
    (out / "scoreboard.md").write_text(board, encoding="utf-8")
    return 0


# ── the numbers ───────────────────────────────────────────────────────────────────────────────

MECHANICAL = ("first_person", "profile_first_person", "colon_bullets", "emdash_violations",
              "cliches", "profile_words", "entries", "bullets", "est_pages", "pages", "ats_issues",
              "invented", "seconds")

DIMENSIONS = ("six_second_pull", "role_angle_fit", "relevance_coverage", "achievement_quality",
              "human_voice", "defensibility", "keyword_integration")


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

    for name in candidates:
        mine = [row for row in rows if row["candidate"] == name]
        lines += [f"### {name}", "",
                  "| variant | n | judge /100 | judged | 1st person | profile 1p | colon bullets | "
                  "em-dash | clichés | profile words | entries | pages | numbers not theirs | seconds |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for variant in variants:
            group = [row for row in mine if row["variant"] == variant]
            if not group:
                continue
            scored = [row["overall_100"] for row in group if row["overall_100"] is not None]
            def avg(key: str, rows_in: list[dict] = group) -> str:
                value = _mean([_value(row, key) for row in rows_in])
                return "n/a" if value is None else f"{value:.1f}"
            lines.append(
                f"| {variant} | {len(group)} "
                f"| {f'{statistics.mean(scored):.1f}' if scored else 'n/a'} "
                f"| {len(scored)}/{len(group)} "
                f"| {avg('first_person')} | {avg('profile_first_person')} "
                f"| {avg('colon_bullets')} | {avg('emdash_violations')} | {avg('cliches')} "
                f"| {avg('profile_words')} | {avg('entries')} | {avg('pages')} | {avg('invented')} "
                f"| {avg('seconds')} |")
        lines.append("")

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
        for key in ("overall_100", *MECHANICAL, *DIMENSIONS):
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
    parser.add_argument("--variants", default="live",
                        help="comma-separated; `live` is libs/prompts/write_cv.md, `uploaded` their own CV")
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
                             "account's rate limit sooner; a 429 is waited out, not fatal")
    parser.add_argument("--model", default=CV_MODEL)
    parser.add_argument("--effort", default=build.EFFORT, help="how hard the model thinks (low … max)")
    parser.add_argument("--judge", action="store_true", help="also run the recruiter verdict")
    parser.add_argument("--dry-run", action="store_true",
                        help="score CVs already in --out, no API calls")
    parser.add_argument("--force", action="store_true", help="rewrite CVs already on disk")
    parser.add_argument("--key-file", default=None)
    parser.add_argument("--out", default="scripts/cvlab/out")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
