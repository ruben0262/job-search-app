"""Is every LinkedIn job in a country stored? A measurement, not a harvest: nothing it finds is kept.

The run reads one list a country, with no search word (`services.harvester.linkedin`). This asks the same
hour three ways (the country's list, as the run asks it; each of its cities' lists; a few word searches,
used here only as a yardstick, never by the harvest) and counts how many of the jobs each shows are stored,
by the harvest's own duplicate check. Asked fresh (`linkedin.unasked`), Ireland's list of its last hour held
54 of the 55 jobs its cities' lists showed and 42 of 43 the word searches did, the one missing a minute old
(2026-11-06). Asked for a round 3,600 seconds it was LinkedIn's copy of an older answer, and held 21.

A job listed in the last `2 * PLATFORM_TICK_MIN` minutes may simply not have been swept yet, so the jobs
listed before that are counted apart: of those, every one missing is one the run did not get.

    set -a; . certificates/server.env; set +a
    python deploy/scripts/check_linkedin_coverage.py Ireland [Germany ...] [--minutes 60]
        [--cities "Dublin,Cork,Limerick"] [--words "engineer,manager"]

About a request per ten jobs each list holds: Ireland's last hour took 44 (2026-11-06), through the site
proxy when HARVEST_PROXY is set, which the describer is spending too.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.corpus.filters.timefilter import parse_date
from libs.corpus.geo import CITIES, COUNTRY_LABELS
from libs.corpus.repository import CorpusRepository
from libs.db import database
from libs.obs import configure
from libs.proxy import site_proxy
from libs.secrets import install, redacting
from libs.settings import PLATFORM_TICK_MIN
from services.harvester import linkedin
from services.harvester.fetch import make_site_client

# Searched for as a yardstick only: what a word search shows is known to be on LinkedIn.
WORDS = ("engineer", "manager", "sales", "nurse", "analyst")


def _listed(client, place: str, seconds: int, word: str = "") -> list:
    """Every job one search lists, to the end of its list, read the way the run reads it."""
    if not word:
        return linkedin._sweep(client, place, seconds).jobs
    sweep, jobs, start = linkedin.Sweep(place), [], 0
    params = {"keywords": word, "location": place, "f_TPR": f"r{seconds}", "sortBy": "DD"}
    while start < linkedin.SWEEP_DEPTH:
        response = linkedin._page(client, {**params, "start": start}, sweep)
        cards = linkedin._cards(response.text) if response is not None and response.status_code == 200 else []
        jobs += cards
        if len(cards) < linkedin.SWEEP_PAGE:
            break
        start += linkedin.SWEEP_PAGE
    return jobs


def coverage(db, client, country: str, *, minutes: int, cities: list[str], words: list[str],
             now: datetime | None = None) -> dict:
    """How many of the jobs each way of asking shows are stored: {way: (listed, stored)}, the same for the
    jobs listed long enough ago that the run has had them, and those missing among them."""
    now = now or datetime.now(UTC)
    corpus, seconds = CorpusRepository(db), linkedin.unasked(minutes * 60, now)
    settled_before = now - timedelta(minutes=2 * PLATFORM_TICK_MIN)
    ways = {"the country's list (what the run reads)": _listed(client, country, seconds)}
    ways[f"its cities' lists ({len(cities)})"] = [job for city in cities
                                                  for job in _listed(client, f"{city}, {country}", seconds)]
    if words:
        ways[f"word searches ({len(words)})"] = [job for word in words
                                                 for job in _listed(client, country, seconds, word)]
    found_by: dict[str, set[str]] = {}
    jobs: dict[str, object] = {}
    for way, listed in ways.items():
        for job in listed:
            jobs[job.url] = job
            found_by.setdefault(job.url, set()).add(way)
    stored = {url for url, job in jobs.items()
              if corpus.find_duplicate(url, job.title, job.company, job.location) is not None}
    settled = {url for url, job in jobs.items()
               if (posted := parse_date(job.date_posted)) is not None and posted <= settled_before}

    def count(urls: set[str]) -> tuple[int, int]:
        return len(urls), len(urls & stored)

    return {"ways": {way: count({job.url for job in listed}) for way, listed in ways.items()},
            "all": count(set(jobs)), "settled": count(settled),
            "missing": [(jobs[url], sorted(found_by[url])) for url in sorted(settled - stored)]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("countries", nargs="+", help="LinkedIn's names: Ireland, Germany, United Kingdom")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--cities", help="comma-separated; if left out, the platform's own in that country")
    parser.add_argument("--words", default=",".join(WORDS), help="comma-separated, or empty for none")
    args = parser.parse_args(argv)
    configure("check_linkedin_coverage")
    install()
    db, proxy = database(), site_proxy()
    print("through the site proxy" if proxy else "direct: HARVEST_PROXY is not set")
    with redacting(proxy or ""), make_site_client(proxy) as client:
        for country in args.countries:
            code = next((code for code, label in COUNTRY_LABELS.items() if label == country), None)
            cities = (args.cities.split(",") if args.cities else
                      [place.label for place in CITIES.values() if code in place.countries])
            words = [word for word in args.words.split(",") if word]
            found = coverage(db, client, country, minutes=args.minutes, cities=cities, words=words)
            print(f"\n{country}, the last {args.minutes} minutes; cities: {', '.join(cities) or 'none'}")
            for way, (listed, stored) in [*found["ways"].items(), ("all of them together", found["all"]),
                                          (f"listed over {2 * PLATFORM_TICK_MIN} min ago", found["settled"])]:
                share = f"{100 * stored / listed:.0f}%" if listed else "-"
                print(f"  {way:44s} {listed:5d} listed {stored:5d} stored  {share:>4s}")
            for job, ways in found["missing"][:15]:
                print(f"  missing: {job.url.rsplit('/', 1)[-1]} {job.title[:40]!r} {job.location!r}, "
                      f"listed {job.date_posted}, shown by {'; '.join(ways)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
