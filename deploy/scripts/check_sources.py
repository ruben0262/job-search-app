"""Is every board's latest work reaching us? One real harvest cycle, then an account of each board.

For every board the harvester has enabled: what it listed for everyone's words this cycle, how fresh
its newest posting was, and how many of those postings are now in the corpus (by the same four-layer
duplicate check the harvest stores with). With `--linkedin EMAIL`, that person's LinkedIn run too,
through the site proxy. A board that lists nothing, fails, or leaves postings out is named.

    set -a; . certificates/dev.env; set +a          # or deploy/.env on the box
    python deploy/scripts/check_sources.py [--linkedin you@example.com]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.corpus.repository import CorpusRepository
from libs.db import database, ensure_schema
from libs.obs import configure
from libs.secrets import install
from services.harvester import linkedin
from services.harvester.__main__ import source_configs_for
from services.harvester.cycle import _run_source, run_cycle
from services.harvester.source_config import ATS_SOURCES
from services.harvester.sources import jobspy_source


def _age(links) -> str:
    """How old the newest posting a board listed is, from the dates it gave."""
    dates = []
    for link in links:
        try:
            moment = datetime.fromisoformat(str(link.date_posted).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        dates.append(moment if moment.tzinfo else moment.replace(tzinfo=UTC))
    if not dates:
        return "no dates"
    hours = (datetime.now(UTC) - max(dates)).total_seconds() / 3600
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def _stored(corpus: CorpusRepository, links) -> int:
    return sum(1 for link in links if link.url and corpus.find_duplicate(
        link.url, link.title, link.company, link.location, application_url=link.application_url))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--linkedin", metavar="EMAIL", help="also run this person's LinkedIn search")
    args = parser.parse_args(argv)
    configure("check_sources")
    install()
    logging.getLogger().setLevel(logging.WARNING)
    ensure_schema()
    db = database()
    corpus = CorpusRepository(db)

    listed: dict[str, list] = {}
    failed: dict[str, str] = {}

    def recording(source, cfg, repo):
        try:
            found = _run_source(source, cfg, repo)
        except Exception as exc:
            failed[source.key] = f"{type(exc).__name__}: {exc}"[:120]
            raise
        listed[source.key] = found
        return found

    configs = source_configs_for(db)
    report, _ = run_cycle(db, source_configs=configs, run_source=recording)
    failed.update({key: str(error)[:120] for key, error in report.source_failures.items()})

    if args.linkedin:
        user = db.users.find_one({"email": args.linkedin.lower()}, {"_id": 1})
        if user is None:
            print(f"no such account: {args.linkedin}", file=sys.stderr)
            return 2
        db.users.update_one({"_id": user["_id"]}, {"$set": {"linkedin_next_run_at": None}})
        found: list = []

        def gather(cfg, log, *, proxy):
            links = jobspy_source.gather_linkedin(cfg, log, proxy=proxy)
            found.extend(links)
            return links

        linkedin.run_for_due_users(db, gather=gather)
        listed["linkedin"] = found

    print(f"\n{'board':16s} {'listed':>7s} {'newest':>8s} {'in corpus':>10s}  note")
    for key in sorted(set(configs) | set(listed) | set(failed)):
        links = listed.get(key, [])
        stored = _stored(corpus, links)
        # A company board answers "not modified" when nothing on it changed since the last poll, so
        # nothing listed there is normal; the harvest log says how many were unchanged.
        empty = ("no board changed since the last poll, or none answered (see the log)" if key in ATS_SOURCES
                 else "listed nothing for these words")
        note = failed.get(key) or (empty if not links else
                                   "" if stored == len(links) else f"{len(links) - stored} not stored")
        print(f"{key:16s} {len(links):7d} {_age(links):>8s} {stored:5d}/{len(links):<4d}  {note}")
    print(f"\ncycle: {report.queries_planned} queries, {report.links_found} links, {report.new_jobs} new, "
          f"{report.duplicates_total} already known")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
