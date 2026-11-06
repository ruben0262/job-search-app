"""Company job boards from a list: each company looked for on every ATS we read, kept if it lists jobs.

    set -a; . certificates/dev.env; set +a          # on this computer
    python deploy/scripts/seed_ats.py [--dry-run] [--ats workable ...]

    docker compose -f deploy/compose.yml run --rm seed-ats [--ats workable]    # on the box

On the box `deploy.sh` runs it, in the background, whenever a deploy brings a change to the list; its
report is in `logs/seed_ats.log`.

The owner asked for it on 2026-11-02. The ATS tier polls the boards in `ats_tokens`, and those only
ever came from links in postings we already held. None of the dev corpus's 1,740 postings links to
one, so nine of the seventeen sources answered nothing every pass.

The names are in `ats_companies.txt` beside this file. Each is tried written together and with
hyphens ("grafanalabs", "grafana-labs") on every ATS whose board is one token, using the harvest's own
fetch code (`services.harvester.sources.ats`), so a board counts here exactly when the harvest could
read it. It counts only if it lists at least one job: SmartRecruiters answers for any name, and an
empty board is nothing to poll. Workday needs a tenant, a data centre and a site, so it is still found
from postings' links only.

What answers goes into `ats_tokens`, with the company's name as the line gives it: Ashby and Lever say
no name, and their jobs would read "Openai". Every harvest polls those boards (with their ETags), and
one that stops answering is marked dead (`AtsRepository.record_poll`). Run again any time: a board
already stored keeps its place and takes the line's name. `--ats` probes only those ATS, to try one
again after it refused a burst.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.corpus.ats import AtsRepository
from libs.corpus.filters.timefilter import is_within_hours
from libs.db import database
from libs.obs import configure
from services.harvester.source_config import PLATFORM_DEFAULTS
from services.harvester.sources import ats

logger = logging.getLogger("seed_ats")

COMPANIES = Path(__file__).with_name("ats_companies.txt")
# The harvest's own, so a name is tried here exactly as a run tries the names its postings carry.
FETCHERS = ats.FETCHERS


def name(line: str) -> str:
    """The company's name, as the line writes it."""
    return line.partition("=")[0].strip()


def tokens(line: str) -> list[str]:
    """The board names one line asks for: its own, if it gives them, or its name two ways."""
    name, _, given = line.partition("=")
    if given.strip():
        return [token.strip().lower() for token in given.split(",") if token.strip()]
    return ats.spellings(name)


def probe(board: str, token: str) -> tuple[str, str, int, int, str | None]:
    """`(ats, token, jobs listed, jobs from the harvest's window, why it failed)`. No board is 0 jobs
    and no failure: the fetchers read a 404 as an empty board."""
    try:
        jobs = FETCHERS[board](token)[0] or []
    except Exception as exc:
        # Counted, not kept: a refusal ("HTTP 429") may be a board missed, worth running again for.
        return board, token, 0, 0, str(exc)[:40] if isinstance(exc, RuntimeError) else type(exc).__name__
    window = PLATFORM_DEFAULTS[board]["posted_within_hours"]
    return board, token, len(jobs), sum(1 for job in jobs if is_within_hours(job.date_posted, window)), None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report the boards, store nothing")
    parser.add_argument("--ats", action="append", choices=sorted(FETCHERS), help="only these ATS")
    args = parser.parse_args()
    configure("seed_ats")

    lines = [line.strip() for line in COMPANIES.read_text(encoding="utf-8").splitlines()]
    # Each token to the name of the first line that asks for it.
    labels: dict[str, str] = {}
    for line in lines:
        if line and not line.startswith("#"):
            for token in tokens(line):
                labels.setdefault(token, name(line))
    names = list(labels)
    found: list[tuple[str, str, int, int, str | None]] = []
    for board in args.ats or FETCHERS:
        # Each ATS on its own, at the concurrency the harvest uses for it: Recruitee refuses a burst.
        with ThreadPoolExecutor(PLATFORM_DEFAULTS[board].get("concurrency", 16)) as pool:
            every = list(pool.map(lambda token, b=board: probe(b, token), names))
        answers = [answer for answer in every if answer[2]]
        failed = Counter(answer[4] for answer in every if answer[4])
        logger.info("%s: %d board(s) of %d names, %d jobs listed, %d recent%s", board, len(answers),
                    len(names), sum(a[2] for a in answers), sum(a[3] for a in answers),
                    f", failed {dict(failed)}" if failed else "")
        found += answers

    logger.info("%d boards from %d lines: %s", len(found),
                sum(1 for line in lines if line and not line.startswith("#")),
                dict(Counter(board for board, *_ in found)))
    for board, token, listed, recent, _ in sorted(found, key=lambda answer: -answer[2])[:40]:
        logger.info("  %-16s %-28s %5d listed %4d recent", board, token, listed, recent)
    if args.dry_run:
        logger.info("--dry-run: nothing stored")
        return
    added = AtsRepository(database()).add_tokens(
        [(board, token) for board, token, *_ in found],
        names={(board, token): labels[token] for board, token, *_ in found})
    logger.info("stored %d new board(s); %d were already there", added, len(found) - added)


if __name__ == "__main__":
    main()
