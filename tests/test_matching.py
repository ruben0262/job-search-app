"""Which jobs a person sees: green words pick them, red words veto them, the order is explainable."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from libs.matching import found, green_words, pattern, underpaid
from tests.conftest import needs_db


def test_a_green_word_matches_whole_words_and_ignores_case_spaces_and_hyphens():
    regex = pattern(green_words({"sections": [{"keywords": ["Java", "front end"]}]}))
    assert regex.search("Senior JAVA developer")
    assert regex.search("Front-End Engineer")
    assert not regex.search("JavaScript developer")


def test_the_words_found_are_said_as_they_are_compared():
    regex = pattern(["backend engineer", "python"])
    assert found(regex, "Backend-Engineer, Python and PYTHON") == {"backend engineer", "python"}
    assert found(regex, "Nurse") == set()


@pytest.mark.parametrize(("text", "minimum", "currency", "dropped"), [
    ("Pays €40,000 - €45,000 per year.", 50_000, "EUR", True),
    ("Pays €40,000 - €55,000 per year.", 50_000, "EUR", False),
    # Converted before it is compared: £40k a year is about €47k.
    ("Salary £40,000 per year.", 50_000, "EUR", True),
    ("Salary £40,000 per year.", 45_000, "EUR", False),
    # The minimum is converted too: £40k is about €47k, which €45k does not reach.
    ("Pays €45,000 per year.", 40_000, "GBP", True),
    # A month is twelve of them: 1.2M forint a month is about €36k a year.
    ("Bruttó 1 200 000 Ft havi.", 40_000, "EUR", True),
    # Unknown is kept: no salary, or no period to tell a month from a year.
    ("Competitive salary and equity.", 50_000, "EUR", False),
    ("Pays €3,000.", 50_000, "EUR", False),
    # And no minimum means nothing is ever dropped for pay.
    ("Pays €10,000 per year.", None, "EUR", False),
])
def test_a_job_is_dropped_for_pay_only_when_its_stated_top_is_known_and_below(
        text, minimum, currency, dropped):
    assert underpaid({"min_salary": minimum, "salary_currency": currency}, text) is dropped


@pytest.fixture()
def account(db, user):
    """One person searching for "backend engineer", with "senior" as a red word."""

    _search(db, user, {"sections": [{"platform": "all", "keywords": ["backend engineer"],
                                     "exclude_keywords": ["senior"]}]})
    return db, user


def _search(db, user, config: dict) -> None:
    from libs.tenant.repository import TenantRepository

    TenantRepository(db).save_config(user.id, config, schema_version=2)


def _jobs(db, *rows):
    from libs.db import insert, now
    from libs.db.models import CorpusJob

    moment = now()
    return [insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title=title,
                                 description=text, is_remote=remote, posted_at=moment,
                                 first_seen_at=moment - timedelta(minutes=age),
                                 described_at=moment - timedelta(minutes=age)))
            for title, text, remote, age in rows]


@needs_db
def test_green_words_pick_jobs_red_words_veto_them_and_titles_rank_first(account):
    from libs.matching import matching_jobs

    db, user = account
    _jobs(db,
          ("Platform Engineer", "You are a backend engineer at heart", True, 1),
          ("Backend Engineer", "Build APIs", True, 2),
          ("Senior Backend Engineer", "Lead the team", True, 3),
          ("Registered Nurse", "Ward rounds", True, 4))

    found = matching_jobs(db, user)

    assert [(m.job.title, m.in_title) for m in found] == [
        ("Backend Engineer", ("backend engineer",)), ("Platform Engineer", ())]
    assert found[0].words == found[1].words == ("backend engineer",)


@needs_db
def test_remote_only_drops_jobs_known_to_be_on_site_but_keeps_unknown_ones(account):
    from libs.matching import matching_jobs

    db, user = account
    _search(db, user, {"sections": [{"platform": "all", "keywords": ["backend engineer"],
                                     "workplaces": ["remote"]}]})
    _jobs(db, ("Backend Engineer A", "", False, 1), ("Backend Engineer B", "", None, 2))

    assert [m.job.title for m in matching_jobs(db, user)] == ["Backend Engineer B"]


@needs_db
def test_a_minimum_salary_drops_what_pays_less_and_keeps_what_does_not_say(account):
    from libs.matching import matching_jobs

    db, user = account
    _search(db, user, {"sections": [{"platform": "all", "keywords": ["backend engineer"]}],
                       "min_salary": 60_000, "salary_currency": "EUR"})
    _low, high, silent = _jobs(db,
                               ("Backend Engineer A", "€40,000 - €50,000 per year", True, 1),
                               ("Backend Engineer B", "€70,000 - €90,000 per year", True, 2),
                               ("Backend Engineer C", "Competitive pay", True, 3))

    assert [m.job.id for m in matching_jobs(db, user)] == [high.id, silent.id]


@needs_db
def test_no_green_words_means_no_jobs_rather_than_every_job(account):
    from libs.matching import matching_jobs, their_jobs

    db, user = account
    _search(db, user, {"sections": [{"platform": "all", "keywords": []}]})
    _jobs(db, ("Backend Engineer", "", True, 1))

    assert matching_jobs(db, user) == []
    # And the Jobs page's own list, which answered 500 here: a plain list has no `cut` (2026-11-11).
    listed = their_jobs(db, user)
    assert listed == [] and listed.cut is False


@needs_db
def test_linkedin_jobs_are_matched_like_any_other_board(account):
    """Fetched through the site's proxy since 2026-11-01, so no proxy of a person's own is needed to see
    them (decision D3, retired)."""
    from libs.matching import matching_jobs

    db, user = account
    linkedin, board = _jobs(db, ("Backend Engineer", "", True, 1), ("Backend Engineer", "", True, 2))
    db.corpus.update_one({"_id": linkedin.id}, {"$set": {"source": "linkedin"}})

    assert {m.job.id for m in matching_jobs(db, user)} == {linkedin.id, board.id}


@needs_db
def test_a_word_anywhere_in_the_description_finds_the_job(account):
    """The whole text is read (owner, 2026-11-02). Only the first 2,500 characters were, which
    missed requirements written below a long introduction: 233 jobs found where the whole finds 300."""
    from libs.matching import matching_jobs

    db, user = account
    deep, = _jobs(db, ("Developer", "About the company. " * 200 + "You are a backend engineer.", True, 1))

    assert [(m.job.id, m.words) for m in matching_jobs(db, user)] == [(deep.id, ("backend engineer",))]


@needs_db
def test_more_of_their_words_ranks_a_job_higher_and_a_title_breaks_the_tie(account):
    """The more of their words a job has, the better it probably is. A title used to outrank
    everything, so one word in a title beat three in the text."""
    from libs.matching import matching_jobs

    db, user = account
    _search(db, user, {"sections": [{"platform": "all",
                                     "keywords": ["backend engineer", "python", "postgres"]}]})
    one_titled, three, one_text = _jobs(db, ("Backend Engineer", "", True, 1),
                                        ("Developer", "Backend engineer: Python, Postgres.", True, 2),
                                        ("Developer", "Some Python.", True, 3))

    assert [m.job.id for m in matching_jobs(db, user)] == [three.id, one_titled.id, one_text.id]


@needs_db
def test_the_text_stays_in_the_database_unless_it_is_asked_for(account):
    """The jobs page needs the words, not the text, and the text was most of what crossed the network.
    An alert asks for it: its card reads the salary off it."""
    from libs.matching import matching_jobs

    db, user = account
    _jobs(db, ("Backend Engineer", "Pays €70,000 per year.", True, 1))

    assert [m.job.description for m in matching_jobs(db, user)] == [None]
    assert [m.job.description for m in matching_jobs(db, user, text=True)] == ["Pays €70,000 per year."]


@needs_db
def test_a_country_shows_every_job_in_it_and_an_old_city_reads_as_its_country(account):
    """My search picks countries (owner, 2026-11-02); a config that still says Berlin means Germany."""
    from libs.corpus.repository import CorpusRepository
    from libs.matching import matching_jobs

    db, user = account
    corpus = CorpusRepository(db)
    berlin, munich, nowhere, kielce = (corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                                  title="Backend Engineer", location=place)
                                       for place in ("Berlin, Germany", "München, Bayern, Germany", "Remote",
                                                     "Kielce"))
    for job_id in (berlin, munich, nowhere, kielce):
        corpus.set_description(job_id, "We build backend systems. " * 10)

    def shown(*places: str) -> set[int]:
        _search(db, user, {"sections": [{"platform": "all", "keywords": ["backend engineer"],
                                         "locations": list(places), "workplaces": []}]})
        return {match.job.id for match in matching_jobs(db, user)}

    assert shown("germany") == {berlin, munich, nowhere}, "a job that names no place is never ruled out"
    assert shown("berlin") == {berlin, munich, nowhere}
    assert shown("hungary") == {nowhere}
    # A posting that names only its town is still in its country, and kept from everybody else's.
    assert shown("poland") == {kielce, nowhere}


@needs_db
def test_a_match_reads_the_newest_postings_with_the_words_and_never_the_older_ones(account):
    """What a match reads grows with the postings recent enough to show, not with all those stored.
    Asked as one question with an "or", at 1.5 million postings the database read every posting with a
    person's words, up to 586,000, and took over three minutes (2026-11-04)."""
    import os

    from pymongo import MongoClient, monitoring

    from libs.db import now
    from libs.db.models import CorpusJob
    from libs.matching import matching_jobs

    db, user = account
    # Older than any job stays (thirty days, owner 2026-11-12), and recent.
    old, recent = now() - timedelta(days=35), now() - timedelta(hours=1)
    db[CorpusJob.COLLECTION].insert_many([
        {"_id": 900_000 + n, "url": f"https://example.com/old/{n}", "source": "test",
         "title": "Backend Engineer", "description": "Build APIs", "first_seen_at": old,
         "posted_at": old, "closed_at": None,
         "describe_failures": 0, "terms": ["backend engineer"], "title_terms": ["backend engineer"],
         "terms_at": old} for n in range(400)])
    _jobs(db, *[("Backend Engineer", "Build APIs", True, 60) for _ in range(5)])
    db.vocabulary.insert_one({"_id": "backend engineer", "indexed_at": recent})

    asked: list[dict] = []

    class Aggregates(monitoring.CommandListener):
        def started(self, event):
            if event.command_name == "aggregate":
                asked.append(event.command)

        def succeeded(self, event):
            pass

        def failed(self, event):
            pass

    watched = MongoClient(os.environ["MONGODB_URI"], uuidRepresentation="standard", tz_aware=True,
                          event_listeners=[Aggregates()])[db.name]
    try:
        assert len(matching_jobs(watched, user)) == 5
        read = 0
        for command in asked:
            plan = watched.command("explain", {"aggregate": command["aggregate"],
                                               "pipeline": command["pipeline"], "cursor": {}},
                                   verbosity="executionStats")
            read += plan.get("stages", [plan])[0].get("$cursor", plan)["executionStats"]["totalDocsExamined"]
    finally:
        watched.client.close()
    assert read <= 5, f"read {read} postings to show 5: the 400 older ones were read too"
