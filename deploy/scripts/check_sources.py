"""Is every board's latest work reaching us? One real harvest cycle, then an account of each board.

For every board the harvester has enabled: what it listed this cycle, how fresh its newest posting
was, and how many of those postings are now in the corpus (by the same four-layer duplicate check the
harvest stores with). With `--linkedin`, the LinkedIn pass too, through the site proxy. A board that
lists nothing, fails, or leaves postings out is named.

    set -a; . certificates/dev.env; set +a          # or certificates/server.env on the server
    python deploy/scripts/check_sources.py [--linkedin]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.corpus.geo import allows, posting_country, wanted_countries
from libs.corpus.repository import CorpusRepository
from libs.db import database, ensure_schema
from libs.obs import configure
from libs.secrets import install
from libs.tenant.repository import TenantRepository
from services.harvester import linkedin
from services.harvester.__main__ import source_configs_for
from services.harvester.cycle import _run_source, run_cycle
from services.harvester.source_config import ATS_SOURCES, union_locations


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


def _elsewhere(links, wanted) -> int:
    """Listed in no country anybody picked: not kept, by design (`cycle._ingest`)."""
    return sum(1 for link in links if not allows(posting_country(link.location, title=link.title), wanted))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--linkedin", action="store_true", help="also run the LinkedIn pass")
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
    report = run_cycle(db, source_configs=configs, run_source=recording)
    failed.update({key: str(error)[:120] for key, error in report.source_failures.items()})

    if args.linkedin:
        found: list = []

        def sweep(places, *, proxy, readers):
            sweeps = linkedin.sweep_places(places, proxy=proxy, readers=readers)
            found.extend(job for each in sweeps for job in each.jobs)
            return sweeps

        linkedin.run_pass(db, sweep=sweep)
        listed["linkedin"] = found

    wanted = wanted_countries(union_locations(TenantRepository(db).active_configs()))
    print(f"\n{'board':16s} {'listed':>7s} {'newest':>8s} {'in corpus':>10s}  note")
    for key in sorted(set(configs) | set(listed) | set(failed)):
        links = listed.get(key, [])
        stored = _stored(corpus, links)
        elsewhere = _elsewhere(links, wanted)
        # A company board answers "not modified" when nothing on it changed since the last poll, so
        # nothing listed there is normal; the harvest log says how many were unchanged.
        empty = ("no board changed since the last poll, or none answered (see the log)" if key in ATS_SOURCES
                 else "listed nothing new")
        missing = len(links) - stored - elsewhere
        note = failed.get(key) or (empty if not links else "; ".join(part for part in (
            f"{elsewhere} in no picked country, not kept" if elsewhere else "",
            f"{missing} not stored" if missing > 0 else "") if part))
        print(f"{key:16s} {len(links):7d} {_age(links):>8s} {stored:5d}/{len(links):<4d}  {note}")
    print(f"\ncycle: {report.links_found} links, {report.new_jobs} new, "
          f"{report.duplicates_total} already known")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
