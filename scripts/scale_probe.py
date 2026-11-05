"""How the platform does at full size: a month of jobs for the EU and the UK, in a throwaway database.

    scripts/scale_probe.py build   [--jobs 1500000]   copies real jobs to that size, spread over 30 days
    scripts/scale_probe.py measure                    the Jobs page for 300 people, a new word, a run's jobs

`MONGODB_URI` is the throwaway database's login, and the database must be called `scale_probe`: the
script refuses any other, and refuses Atlas. `SOURCE_URI` and `SOURCE_DB` are where the real jobs are
copied from (by default this laptop's `jobsearch_dev`); nothing is written there. Measured first on
2026-11-04 (CHANGELOG): a laptop on two cores with a 1.5 GB cache, like server_db's VM.
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MONGODB_DB"] = "scale_probe"

from libs.corpus.repository import CorpusRepository
from libs.corpus.terms import catch_up, terms_of
from libs.db import ensure_schema, now
from libs.db.models import User, Word
from libs.matching import their_jobs
from services.harvester.cycle import CycleReport, _ingest
from services.harvester.sources.base import JobLink

PROBE = "scale_probe"
# How many real jobs are copied to make the rest: 24,452 on 2026-11-04's measurement.
SOURCE_JOBS = 25_000
# What people search for: job titles and skills across trades, so the words index is as busy as a real one.
WORDS = """python java javascript typescript react angular node golang rust php ruby kotlin swift sql
postgresql mysql mongodb oracle excel tableau sap salesforce aws azure gcp docker kubernetes terraform
linux devops security tester nurse doctor pharmacist physiotherapist caregiver dentist midwife
paramedic psychologist teacher tutor lecturer researcher scientist chemist biologist accountant
bookkeeper auditor tax controller payroll treasury receptionist secretary recruiter copywriter
photographer journalist translator interpreter electrician plumber welder carpenter mechanic forklift
logistics procurement buyer planner dispatcher driver courier chef cook waiter barista bartender
housekeeping cleaner cashier lawyer paralegal compliance underwriter banking investment trader actuary
consultant architect designer german english french spanish dutch italian polish hungarian remote
hybrid junior senior intern""".split()  # noqa: SIM905 - read as the word list it is
PHRASES = ("software engineer", "data analyst", "data engineer", "data scientist", "machine learning",
           "project manager", "product manager", "business analyst", "full stack", "test automation",
           "scrum master", "team lead", "social worker", "financial analyst", "sales manager",
           "account manager", "business development", "customer service", "office manager",
           "marketing manager", "digital marketing", "graphic designer", "ux designer",
           "mechanical engineer", "electrical engineer", "civil engineer", "quality engineer",
           "maintenance technician", "machine operator", "supply chain", "truck driver", "security guard",
           "store manager", "real estate", "power bi")
VOCABULARY = sorted(set(WORDS) | set(PHRASES))
# New LinkedIn jobs per 15 minutes at 11:00 on 2026-11-04, by country; None is a job that names none.
COUNTRIES = {"GB": 386, "DE": 215, "FR": 199, "NL": 156, "ES": 147, "IT": 107, "PL": 60, "IE": 50,
             "BE": 46, "SE": 41, "AT": 40, "DK": 35, "RO": 30, "PT": 26, "CZ": 20, None: 120}
# The same countries as My search stores them.
SLUGS = {"GB": "united_kingdom", "DE": "germany", "FR": "france", "NL": "netherlands", "ES": "spain",
         "IT": "italy", "PL": "poland", "IE": "ireland", "BE": "belgium", "SE": "sweden", "AT": "austria",
         "DK": "denmark", "RO": "romania", "PT": "portugal", "CZ": "czechia"}


def _probe():
    uri = os.environ.get("MONGODB_URI", "")
    if not uri or "mongodb.net" in uri or uri.startswith("mongodb+srv"):
        sys.exit("MONGODB_URI must be the throwaway database's login, never Atlas")
    return MongoClient(uri, uuidRepresentation="standard", tz_aware=True)[PROBE]


def _people(db, rng: random.Random) -> None:
    """300 people searching for 3 to 12 words each, in one to three countries."""
    for n in range(300):
        user_id, places = uuid.uuid4(), rng.sample(list(SLUGS), rng.choice([1, 1, 1, 2, 3]))
        db.users.insert_one({"_id": user_id, "email": f"u{n}@probe.test", "password_hash": "x",
                             "status": "active", "active": True, "timezone": "UTC", "limits": {}})
        db.user_config.insert_one({"user_id": user_id, "schema_version": 2, "config": {
            "languages": ["en"], "sections": [{
                "platform": "all", "keywords": rng.sample(VOCABULARY, rng.randint(3, 12)),
                "locations": [SLUGS[code] for code in places],
                "workplaces": ["remote", "hybrid", "onsite"]}]}})
    db[Word.COLLECTION].insert_many([{"_id": word, "indexed_at": now()} for word in VOCABULARY])


def build(jobs: int) -> None:
    rng = random.Random(4)  # noqa: S311 - made-up data, the same each run
    db = _probe()
    ensure_schema(db)
    if not db.users.count_documents({}):
        _people(db, rng)
    source = MongoClient(os.environ.get("SOURCE_URI", "mongodb://127.0.0.1:27017"), tz_aware=True)[
        os.environ.get("SOURCE_DB", "jobsearch_dev")]
    # The newest SOURCE_JOBS of them, copied over and over: enough real variety, and inside the harvester
    # container's 1 GB, which a whole corpus held in memory at once is not.
    rows = list(source.corpus.find({}, sort=[("first_seen_at", -1)], limit=SOURCE_JOBS))
    started = time.monotonic()
    for row in rows:
        row.update(terms_of(row.get("title"), row.get("description"), VOCABULARY))
    print(f"{len(rows):,} real jobs to copy, words found in {time.monotonic() - started:.0f} s", flush=True)

    codes, weights = list(COUNTRIES), list(COUNTRIES.values())
    have, batch, at, started = db.corpus.estimated_document_count(), [], now(), time.monotonic()
    while have + len(batch) < jobs:
        number = 10_000_000 + have + len(batch)
        row = rows[number % len(rows)]
        seen = at - timedelta(seconds=rng.uniform(0, 30 * 86400))
        batch.append({**row, "_id": number, "url": f"{row['url']}#p{number}",
                      "canonical_url": f"{row.get('canonical_url') or row['url']}#p{number}",
                      "content_key": f"{row.get('content_key') or ''}:{number}", "first_seen_at": seen,
                      "posted_at": seen - timedelta(minutes=rng.uniform(5, 120)), "terms_at": seen,
                      "closed_at": None, "describe_failures": 0, "country": rng.choices(codes, weights)[0]})
        if len(batch) == 10_000:
            db.corpus.insert_many(batch, ordered=False)
            have, batch = have + len(batch), []
            if have % 250_000 == 0:
                print(f"  {have:,} jobs, {time.monotonic() - started:.0f} s", flush=True)
    if batch:
        db.corpus.insert_many(batch, ordered=False)
    stats = db.command("collStats", "corpus")
    print(f"{stats['count']:,} jobs: {stats['size'] / 1e9:.2f} GB of data, "
          f"{stats['storageSize'] / 1e9:.2f} GB on disk, indexes {stats['totalIndexSize'] / 1e9:.2f} GB")


def _pages(db, users) -> None:
    """Everybody's Jobs page, the first time (from disk) and again (from the cache)."""
    for label in ("first time", "again"):
        runs = []
        for user in users:
            started = time.monotonic()
            listed = len(their_jobs(db, user))
            runs.append((time.monotonic() - started, listed))
        times = sorted(seconds for seconds, _ in runs)
        print(f"Jobs page, {label}: median {statistics.median(times) * 1000:.0f} ms, 90% under "
              f"{times[int(len(times) * 0.9)] * 1000:.0f} ms, slowest {times[-1] * 1000:.0f} ms; jobs "
              f"listed: median {statistics.median(n for _, n in runs):.0f}, most {max(n for _, n in runs)}",
              flush=True)


def measure() -> None:
    db = _probe()
    users = [User.from_doc(row) for row in db.users.find({})]
    print(f"{db.corpus.estimated_document_count():,} jobs, {len(users)} people", flush=True)
    _pages(db, users)

    for word in ("agile", "blockchain"):
        db.user_config.update_one({"user_id": users[0].id}, {"$push": {"config.sections.0.keywords": word}})
        started = time.monotonic()
        before = len(their_jobs(db, users[0]))
        waited, started = time.monotonic() - started, time.monotonic()
        report = catch_up(db)
        print(f"a new word, '{word}': their page before it was indexed {waited * 1000:.0f} ms ({before} "
              f"jobs); found in {report.backfilled:,} stored jobs in {time.monotonic() - started:.0f} s",
              flush=True)

    rows = list(db.corpus.aggregate([{"$sample": {"size": 1800}}, {"$project": {
        "title": 1, "company": 1, "location": 1, "description": 1}}]))
    links = [JobLink(url=f"https://www.linkedin.com/jobs/view/{90_000_000 + n}", title=row["title"],
                     company=f"{row.get('company') or ''} {n}", location=row.get("location") or "",
                     source="linkedin", date_posted=datetime.now(UTC).date().isoformat())
             for n, row in enumerate(rows)]
    corpus = CorpusRepository(db)
    started = time.monotonic()
    new = _ingest(corpus, links, CycleReport())
    stored, started = time.monotonic() - started, time.monotonic()
    ids = {row["url"]: row["_id"]
           for row in db.corpus.find({"url": {"$in": [link.url for link in links]}}, {"url": 1})}
    for link, row in zip(links, rows, strict=True):
        corpus.set_description(ids[link.url], row.get("description") or "")
    texts, started = time.monotonic() - started, time.monotonic()
    report = catch_up(db)
    print(f"one run's {new:,} new jobs: stored in {stored:.1f} s, their text saved in {texts:.1f} s, "
          f"their words found in {time.monotonic() - started:.1f} s ({report.indexed:,})", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("build", "measure"))
    parser.add_argument("--jobs", type=int, default=1_500_000)
    args = parser.parse_args()
    if args.step == "build":
        build(args.jobs)
    else:
        measure()


if __name__ == "__main__":
    main()
