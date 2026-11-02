"""Company job boards from a list: each company looked for on every ATS we read, kept if it lists jobs.

    set -a; . certificates/dev.env; set +a          # or deploy/.env on the box
    python deploy/scripts/seed_ats.py [--dry-run] [--ats workable ...]

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
import re
import sys
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.corpus.ats import AtsRepository
from libs.corpus.filters.timefilter import is_within_hours
from libs.db import database
from services.harvester.source_config import PLATFORM_DEFAULTS
from services.harvester.sources import ats

COMPANIES = Path(__file__).with_name("ats_companies.txt")
FETCHERS = {"greenhouse": ats.fetch_greenhouse, "lever": ats.fetch_lever, "ashby": ats.fetch_ashby,
            "workable": ats.fetch_workable, "recruitee": ats.fetch_recruitee,
            "personio": ats.fetch_personio, "smartrecruiters": ats.fetch_smartrecruiters,
            "breezy": ats.fetch_breezy}


def name(line: str) -> str:
    """The company's name, as the line writes it."""
    return line.partition("=")[0].strip()


def tokens(line: str) -> list[str]:
    """The board names one line asks for: its own, if it gives them, or its name two ways."""
    name, _, given = line.partition("=")
    if given.strip():
        return [token.strip().lower() for token in given.split(",") if token.strip()]
    plain = unicodedata.normalize("NFKD", name.casefold()).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", plain)
    return list(dict.fromkeys(["".join(words), "-".join(words)])) if words else []


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
        print(f"{board:16s} {len(answers):4d} board(s) of {len(names)} names, "
              f"{sum(a[2] for a in answers):6d} jobs listed, {sum(a[3] for a in answers):5d} recent"
              f"{f', failed {dict(failed)}' if failed else ''}", flush=True)
        found += answers

    print(f"\n{len(found)} boards from {sum(1 for line in lines if line and not line.startswith('#'))} "
          f"lines: {dict(Counter(board for board, *_ in found))}")
    for board, token, listed, recent, _ in sorted(found, key=lambda answer: -answer[2])[:40]:
        print(f"  {board:16s} {token:28s} {listed:5d} listed {recent:4d} recent")
    if args.dry_run:
        print("\n--dry-run: nothing stored")
        return
    added = AtsRepository(database()).add_tokens(
        [(board, token) for board, token, *_ in found],
        names={(board, token): labels[token] for board, token, *_ in found})
    print(f"\nstored {added} new board(s); {len(found) - added} were already there")


if __name__ == "__main__":
    main()
