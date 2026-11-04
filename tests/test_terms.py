"""Each posting's words, stored on it, so a person's jobs are an index lookup and not a regex over
every description: the same jobs with the same words either way, nothing missed while the harvester
has not searched yet, and words added later found in postings already searched."""
from __future__ import annotations

import uuid

import pytest

from libs.corpus.terms import catch_up, found
from tests.conftest import needs_db

TEXT = "We build payment systems in Python and Postgres, on call one week in six. " * 4


def test_each_word_is_found_on_its_own_even_inside_another():
    """One regex of every word finds "backend engineer" and passes over the "engineer" inside it."""
    words = ["backend engineer", "engineer", "front end", "c++", "java"]
    assert found("Senior Backend-Engineer, C++ and Front End", words) == {
        "backend engineer", "engineer", "front end", "c++"}
    assert found("JavaScript developer", words) == set(), "a whole word, not a piece of one"


@pytest.fixture()
def account(db, user):
    from libs.tenant.repository import TenantRepository

    def search(*words: str) -> None:
        config = {"sections": [{"platform": "all", "keywords": list(words)}]}
        TenantRepository(db).save_config(user.id, config, schema_version=2)

    search("backend engineer", "python")
    return db, user, search


def _stored(db, title: str, text: str | None = TEXT, location: str = "Berlin, Germany") -> int:
    from libs.corpus.repository import CorpusRepository

    corpus = CorpusRepository(db)
    job_id = corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="test", title=title,
                        location=location)
    if text:
        corpus.set_description(job_id, text)
    return job_id


def _found(db, user) -> dict[int, tuple]:
    from libs.matching import matching_jobs

    return {match.job.id: (match.words, match.in_title) for match in matching_jobs(db, user)}


@needs_db
def test_the_same_jobs_with_the_same_words_before_and_after_the_words_are_stored(account):
    db, user, _ = account
    titled = _stored(db, "Backend Engineer", "Build APIs. " * 30)
    in_text = _stored(db, "Developer")
    _stored(db, "Registered Nurse", "Ward rounds. " * 30)
    before = _found(db, user)

    report = catch_up(db)

    assert report.new_words == 2 and report.indexed == 3
    assert _found(db, user) == before == {titled: (("backend engineer",), ("backend engineer",)),
                                          in_text: (("python",), ())}


@needs_db
def test_a_searched_posting_is_matched_on_its_stored_words_not_read_again(account):
    """What makes it fast: once a posting's words are stored, its text is not read to match it."""
    from libs.db.models import CorpusJob

    db, user, _ = account
    job = _stored(db, "Developer")
    catch_up(db)
    db[CorpusJob.COLLECTION].update_one({"_id": job}, {"$set": {"description": "Nothing of theirs here."}})

    assert _found(db, user) == {job: (("python",), ())}


@needs_db
def test_new_text_is_read_at_once_and_searched_on_the_next_pass(account):
    """A posting described after the pass is not missed in between: it is read the slow way."""
    from libs.corpus.repository import CorpusRepository
    from libs.db import get
    from libs.db.models import CorpusJob

    db, user, _ = account
    job = _stored(db, "Developer", text=None)
    catch_up(db)
    assert _found(db, user) == {}

    CorpusRepository(db).set_description(job, TEXT)
    assert get(db, CorpusJob, job).terms_at is None, "new text, searched again"
    assert _found(db, user) == {job: (("python",), ())}
    catch_up(db)
    assert get(db, CorpusJob, job).terms == ["python"]
    assert _found(db, user) == {job: (("python",), ())}


@needs_db
def test_a_word_added_later_is_found_at_once_and_stored_in_postings_already_searched(account):
    from libs.db import get
    from libs.db.models import CorpusJob

    db, user, search = account
    job = _stored(db, "Developer")
    catch_up(db)

    search("python", "postgres")
    assert _found(db, user) == {job: (("postgres", "python"), ())}, "before the harvester searched for it"
    report = catch_up(db)

    assert (report.new_words, report.backfilled, report.indexed) == (1, 1, 0), "no posting read again whole"
    assert set(get(db, CorpusJob, job).terms) == {"postgres", "python"}
    assert _found(db, user) == {job: (("postgres", "python"), ())}


@needs_db
def test_a_new_word_is_searched_for_a_stretch_at_a_time_and_its_jobs_found_meanwhile(account):
    """At 1.5 million postings, searching all of them for "agile" took 612 s, past the harvest's 14 minutes,
    so it would have started again every run and never finished (2026-11-04). It goes back a stretch at a
    time, keeps where it got to, and its jobs are found the slow way until it is done."""
    from datetime import timedelta

    from libs.db import get, now
    from libs.db.models import CorpusJob, Word

    db, user, search = account
    jobs = [_stored(db, "Developer") for _ in range(3)]
    # Three of the stretches it searches in, each one hour inside them.
    for hours, job in zip((1, 8, 14), jobs, strict=True):
        db[CorpusJob.COLLECTION].update_one({"_id": job},
                                            {"$set": {"first_seen_at": now() - timedelta(hours=hours)}})
    catch_up(db)

    search("python", "postgres")
    first = catch_up(db, budget_seconds=0)
    assert first.new_words == 1 and get(db, Word, "postgres").indexed_at is None, "one stretch, no more"
    assert "postgres" in get(db, CorpusJob, jobs[0]).terms
    assert "postgres" not in get(db, CorpusJob, jobs[1]).terms
    assert set(_found(db, user)) == set(jobs), "its jobs are found meanwhile, the slow way"

    runs = 1
    while get(db, Word, "postgres").indexed_at is None and runs < 100:
        catch_up(db, budget_seconds=0)
        runs += 1
    assert get(db, Word, "postgres").indexed_at is not None and runs > 2, "a stretch a run, and then done"
    assert all("postgres" in get(db, CorpusJob, job).terms for job in jobs)
    assert set(_found(db, user)) == set(jobs)


@needs_db
def test_adding_a_word_keeps_every_job_the_other_words_found_while_it_is_searched_for(account):
    """Until a new word was searched for, every word was read the slow way, in the newest candidates only:
    adding one cut a person's list from ~1,500 jobs to 458 for up to a run (2026-11-04)."""
    from datetime import timedelta

    from libs.db import now
    from libs.db.models import CorpusJob
    from libs.matching import matching_jobs

    db, user, search = account
    theirs = [_stored(db, "Developer") for _ in range(2)]
    newer = [_stored(db, "Registered Nurse", "Ward rounds. " * 30) for _ in range(3)]
    for hours, job in zip((5, 4, 3, 2, 1), [*theirs, *newer], strict=True):
        db[CorpusJob.COLLECTION].update_one({"_id": job},
                                            {"$set": {"first_seen_at": now() - timedelta(hours=hours)}})
    catch_up(db)

    search("python", "golang")

    assert {match.job.id for match in matching_jobs(db, user, scan=3)} == set(theirs)


@needs_db
def test_a_new_word_is_not_searched_for_in_postings_too_old_to_be_shown(account):
    """Nothing older than the widest window any board has is matched, so it is not searched either."""
    from datetime import timedelta

    from libs.db import get, now
    from libs.db.models import CorpusJob

    db, _, search = account
    recent, old = _stored(db, "Developer"), _stored(db, "Developer")
    db[CorpusJob.COLLECTION].update_one({"_id": old}, {"$set": {"first_seen_at": now() - timedelta(days=25)}})
    catch_up(db)

    search("python", "postgres")
    catch_up(db)

    assert "postgres" in get(db, CorpusJob, recent).terms
    assert "postgres" not in get(db, CorpusJob, old).terms


@needs_db
def test_postings_stored_before_this_get_their_city_country_and_described_time_once(account):
    """Rows written before these fields existed are given them on the first pass."""
    from libs.db import get, insert, now
    from libs.db.models import CorpusJob

    db, _user, _ = account
    old = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Developer",
                               location="Warszawa, Poland", description=TEXT, description_fetched_at=now()))
    thin = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Developer",
                                location="Remote", description="Short."))
    town = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Developer",
                                location="Kielce", description=TEXT))
    fields = {"city": "", "described_at": "", "terms": "", "terms_at": ""}
    db[CorpusJob.COLLECTION].update_many({"_id": {"$in": [old.id, thin.id, town.id]}}, {"$unset": fields})

    catch_up(db)

    assert (get(db, CorpusJob, old.id).city, get(db, CorpusJob, thin.id).city) == ("warsaw", None)
    assert get(db, CorpusJob, town.id).country == "PL", "read again, now a town anywhere names a country"
    assert get(db, CorpusJob, old.id).described_at is not None
    assert get(db, CorpusJob, thin.id).described_at is None, "a teaser is not a whole job"
