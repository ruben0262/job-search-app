"""The CV lab: run tailoring-prompt variants over the same postings and compare the numbers.

Nothing here writes to a prompt. Variant `live` reads `libs/scaffolds/tailor_cv.md` at run time, so
the baseline is always the prompt a new account is actually seeded with and cannot drift from it;
every other variant is a separate file under `variants/`. `CLAUDE.md` §3a: the live prompt is
measured, never edited, and a variant becomes the live prompt only when the owner says so.

    # score CVs already on disk — no API calls, no credential, no money
    python scripts/cvlab/run.py --dry-run --out scripts/cvlab/out/ab

    # the comparison this exists for: three samples of each prompt over every posting
    python scripts/cvlab/run.py --variants live,d-both --samples 3 --judge --out scripts/cvlab/out/ab

Credential, in order: `--key-file`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`.

**Why samples and pairing.** One generation is not a result: the model varies run to run, so a
single CV per cell cannot tell a better prompt from a luckier one. Every cell is run `--samples`
times, differences are taken *paired* — same candidate, same posting, live minus variant — and the
spread is reported beside the mean. Postings carry a `group` and all copies of a group stay on the
same side of any split, because this platform's corpus lists one role in a dozen cities and each
copy has its own `content_key`.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import pathlib
import statistics
import sys
import time
from concurrent import futures

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from libs.llm import client_for  # noqa: E402
from libs.profiles import GENERATION_FILES, PLATFORM_FILES, platform_file  # noqa: E402
from libs.settings import CV_MODEL  # noqa: E402
from libs.templates import split_style  # noqa: E402
from scripts.cvlab import cvchecks  # noqa: E402
from services.docgen import build  # noqa: E402

logger = logging.getLogger("cvlab")

HERE = pathlib.Path(__file__).resolve().parent
LIVE_PROMPT = REPO / "libs" / "scaffolds" / "tailor_cv.md"
JUDGE_PROMPT = REPO / "libs" / "prompts" / "eval_cv.md"

# Held fixed while the writing prompt varies: the judge is the instrument, not the thing measured.
JUDGE_MODEL = "claude-sonnet-5"

# Seven dimensions, each with a quoted line, plus three fixes. coverlab lost half a grid to a judge
# reply truncated mid-JSON that scored as `None` and was quietly averaged over, so this is generous.
JUDGE_MAX_TOKENS = 6_000

# The markers `setup-prompts` fills for a real account. The bench fills them from the candidate's
# own data for the same reason: `build._require_personalised` refuses a prompt that still carries
# one, and it is right to — a CV addressed to `<<NAME>>` would reach an employer.
MARKERS = {"<<NAME>>": "name", "<<LANGUAGES>>": "languages",
           "<<WORK_ELIGIBILITY>>": "work_eligibility", "<<HOME_LOCATION>>": "home_location"}


class Posting:
    """One job fixture, shaped like the corpus row `build.job_context` reads."""

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
        self.applicants = data.get("applicants")
        self.is_remote = data.get("is_remote")
        self.posted_at = None
        self.application_url = None


class Candidate:
    """One real account's profile files, pulled out of production into `candidates/<name>/`.

    Never in git (`.gitignore`): these are two people's CVs, career histories and writing prompts.
    Re-pull with, per file:

        docker exec job-search-platform-postgres-1 psql -U jobsearch -d jobsearch -At -c \\
          "SELECT f.content FROM user_profile_files f JOIN users u ON u.id=f.user_id \\
           WHERE u.email='…' AND f.name='…';"
    """

    def __init__(self, name: str, root: pathlib.Path):
        folder = root / name
        if not folder.is_dir():
            raise SystemExit(f"no candidate at {folder} — see the docstring for how to pull one")
        self.name = name
        self.files = {path.name: path.read_text(encoding="utf-8")
                      for path in folder.glob("*") if path.name != "candidate.json"}
        self.identity = json.loads((folder / "candidate.json").read_text(encoding="utf-8"))

        missing = [f for f in ("base_cv.html", "context.md") if f not in self.files]
        if missing:
            raise SystemExit(f"candidate {name} is missing {', '.join(missing)}")
        self.base_cv = self.files["base_cv.html"]
        self.role_angles = self.files.get("08-role-angles.md")

    def grounding(self) -> str:
        """The system prompt, assembled exactly as `libs.profiles.grounding` does in production."""
        sections = [platform_file(name) for name in PLATFORM_FILES]
        sections += [self.files[name] for name in GENERATION_FILES if name in self.files]
        return "\n\n---\n\n".join(sections)

    def fill(self, template: str) -> str:
        """Put this person into the prompt, the way `setup-prompts` does for a real account."""
        for marker, key in MARKERS.items():
            template = template.replace(marker, self.identity[key])
        return template


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


def load_variant(name: str) -> str:
    """`live` is the seeded prompt, read fresh. Anything else is a file under `variants/`."""
    path = LIVE_PROMPT if name == "live" else HERE / "variants" / f"{name}.md"
    if not path.exists():
        raise SystemExit(f"variant {name!r} not found at {path}")
    return path.read_text(encoding="utf-8")


_OAUTH_PREFIX = "sk-ant-oat"


def credential(key_file: str | None) -> tuple[str, str]:
    """The credential and which kind it is. The value is never logged."""
    if key_file:
        value = pathlib.Path(key_file).read_text(encoding="utf-8").strip()
        return value, kind_of(value)
    for variable in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        value = os.environ.get(variable)
        if value:
            return value.strip(), kind_of(value.strip())
    raise SystemExit("no credential: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN, "
                     "or pass --key-file")


def kind_of(value: str) -> str:
    return "claude_oauth" if value.startswith(_OAUTH_PREFIX) else "claude_api"


# ── one cell of the grid ──────────────────────────────────────────────────────────────────────

def write_one(client, candidate: Candidate, posting: Posting, *, template: str,
              model: str) -> tuple[str, float]:
    """One CV, through the production call. Returns the HTML and the seconds it took."""
    context = build.job_context(posting, description=posting.description)
    started = time.monotonic()
    # The candidate's own look: the bench compares wordings, so the layout stays the one it always had.
    html = build.tailor(client, candidate.base_cv, context, style=split_style(candidate.base_cv)[1],
                        facts_md=candidate.grounding(), template=candidate.fill(template),
                        role_angles=candidate.role_angles, model=model)
    return html, time.monotonic() - started


def judge_one(client, posting: Posting, html: str, report: dict) -> dict | None:
    """The recruiter's verdict, on what no regular expression reaches."""
    prompt = (JUDGE_PROMPT.read_text(encoding="utf-8")
              .replace("<DETERMINISTIC_SUMMARY>", json.dumps(report))
              .replace("<JOB_DESCRIPTION>", posting.description)
              .replace("<CV_TEXT>", cvchecks.text_of(html)))
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


def _verdict(client, posting: Posting, html: str, report: dict, *, kept: pathlib.Path,
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
    verdict = judge_one(client, posting, html, report)
    if verdict is not None:
        kept.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    return verdict


class _LazyClient:
    """The API client, created the first time something actually calls it."""

    def __init__(self, key_file: str | None):
        self._key_file = key_file
        self._client = None

    def __getattr__(self, name: str):
        if self._client is None:
            value, kind = credential(self._key_file)
            self._client = client_for(value, kind)
            logger.info("credential: %s", kind)
        return getattr(self._client, name)


def _run_cell(client, candidate: Candidate, posting: Posting, *, variant: str, sample: int,
          template: str | None, out: pathlib.Path, args) -> dict | None:
    """One CV and its verdict, generating whichever half is not already on disk."""
    path = out / f"{candidate.name}__{variant}__{posting.slug}__{sample}.html"

    if args.dry_run:
        if not path.exists():
            return None
        html, seconds = path.read_text(encoding="utf-8"), 0.0
    elif path.exists() and not args.force:
        # A CV already written is a CV already paid for.
        html, seconds = path.read_text(encoding="utf-8"), 0.0
    else:
        html, seconds = _with_backoff(
            lambda: write_one(client, candidate, posting, template=template, model=args.model),
            what=f"{candidate.name}/{variant}/{posting.slug}#{sample}")
        path.write_text(html, encoding="utf-8")

    report = cvchecks.check(html)
    verdict = _verdict(client, posting, html, report, kept=path.with_suffix(".verdict.json"),
                       judge=args.judge, force=args.force)
    dimensions = {key: value.get("score")
                  for key, value in ((verdict or {}).get("dimensions") or {}).items()}
    row = {"candidate": candidate.name, "variant": variant, "job": posting.slug,
           "group": posting.group, "sample": sample, "seconds": round(seconds, 1), **report,
           "violations": cvchecks.violations(report),
           "overall_100": (verdict or {}).get("overall_100"),
           "verdict": (verdict or {}).get("verdict"),
           "one_line": (verdict or {}).get("one_line"),
           "dimensions": dimensions,
           "top_fixes": (verdict or {}).get("top_fixes") or []}
    logger.info("%-7s %-10s %-42s #%d  1p=%-2d colon=%-2d pages~%.1f judge=%s",
                candidate.name, variant, posting.slug[:42], sample, report["first_person"],
                report["colon_bullets"], report["est_pages"], row["overall_100"])
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

    candidates = {name: Candidate(name, pathlib.Path(args.candidates_dir))
                  for name in args.candidates.split(",")}
    variants = args.variants.split(",")

    # Built on first use, never on start-up. `--dry-run --judge` over a grid whose verdicts are
    # all cached makes no call at all, and the docstring promises that costs nothing — it was
    # refusing to start without a credential it would never have used.
    client = _LazyClient(args.key_file)

    # Every cell up front, so the work can be spread across threads instead of across processes.
    # Four processes was the previous shape and it cost a merge step, four log files, and a rate
    # limit nobody could see coming because no one process knew what the others were spending.
    cells = []
    for name, candidate in candidates.items():
        postings = load_postings(jobs_root, name, args.only.split(",") if args.only else None)
        for variant in variants:
            template = None if args.dry_run else load_variant(variant)
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
    return _finish(rows, out)


def _finish(rows: list[dict], out: pathlib.Path) -> int:
    if not rows:
        logger.warning("nothing was measured, so nothing was written")
        return 1
    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    board = scoreboard(rows)
    print(board)
    (out / "scoreboard.md").write_text(board, encoding="utf-8")
    return 0


# ── the numbers ───────────────────────────────────────────────────────────────────────────────

MECHANICAL = ("first_person", "profile_first_person", "colon_bullets", "emdash_violations",
              "cliches", "profile_words", "entries", "bullets", "est_pages")

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


def scoreboard(rows: list[dict]) -> str:
    """Per-variant means, then the paired comparison, then what did not move."""
    variants = list(dict.fromkeys(row["variant"] for row in rows))
    # `live` is the baseline whenever it was run, so every difference reads "variant minus the
    # deployed prompt" no matter which arm's results.json was merged first. It was decided by glob
    # order, which flipped the sign of the whole comparison depending on a folder name.
    if "live" in variants:
        variants = ["live"] + [name for name in variants if name != "live"]
    candidates = list(dict.fromkeys(row["candidate"] for row in rows))
    lines: list[str] = []

    for name in candidates:
        mine = [row for row in rows if row["candidate"] == name]
        lines += [f"### {name}", "",
                  "| variant | n | judge /100 | judged | 1st person | profile 1p | colon bullets | "
                  "em-dash | clichés | profile words | entries | pages~ |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
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
                f"| {avg('profile_words')} | {avg('entries')} | {avg('est_pages')} |")
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
                        help="comma-separated; `live` is libs/scaffolds/tailor_cv.md")
    parser.add_argument("--candidates", default="andras,ruben")
    parser.add_argument("--candidates-dir", default="scripts/cvlab/candidates")
    parser.add_argument("--jobs-dir", default="scripts/cvlab/jobs")
    parser.add_argument("--only", default="", help="comma-separated fixture slugs; default all")
    parser.add_argument("--samples", type=int, default=3,
                        help="generations per cell; one is not a result")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="cells in flight at once. Higher finishes sooner and trips the "
                             "account's rate limit sooner; a 429 is waited out, not fatal")
    parser.add_argument("--model", default=CV_MODEL)
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
