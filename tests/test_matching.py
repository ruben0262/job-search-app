"""Which jobs a person sees: green words pick them, red words veto them, the order is explainable."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.matching import TEXT_POINTS, TITLE_POINTS, green_words, pattern, score, underpaid
from tests.conftest import needs_db


def test_a_green_word_matches_whole_words_and_ignores_case_spaces_and_hyphens():
    regex = pattern(green_words({"sections": [{"keywords": ["Java", "front end"]}]}))
    assert regex.search("Senior JAVA developer")
    assert regex.search("Front-End Engineer")
    assert not regex.search("JavaScript developer")


def test_a_title_match_outranks_a_text_match_and_says_which_words_matched():
    regex = pattern(["backend engineer", "python"])
    assert score(regex, "Backend Engineer", "") == (TITLE_POINTS, ("backend engineer",))
    assert score(regex, "Platform role", "We use Python daily") == (TEXT_POINTS, ("python",))
    assert score(regex, "Backend-Engineer", "Python") == (100, ("backend engineer", "python"))
    assert score(regex, "Nurse", "Ward rounds") == (0, ())


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
                                 first_seen_at=moment - timedelta(minutes=age)))
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

    assert [(m.job.title, m.score) for m in found] == [
        ("Backend Engineer", TITLE_POINTS), ("Platform Engineer", TEXT_POINTS)]
    assert found[0].words == ("backend engineer",)


@needs_db
def test_since_only_returns_jobs_first_seen_after_it(account):
    from libs.matching import matching_jobs

    db, user = account
    _old, new = _jobs(db, ("Backend Engineer", "", True, 60), ("Backend Engineer II", "", True, 1))

    found = matching_jobs(db, user, since=datetime.now(UTC) - timedelta(minutes=30))

    assert [m.job.id for m in found] == [new.id]


@needs_db
def test_remote_only_drops_jobs_known_to_be_on_site_but_keeps_unknown_ones(account):
    from libs.matching import matching_jobs

    db, user = account
    _search(db, user, {"sections": [{"platform": "all", "keywords": ["backend engineer"],
                                     "remote_policy": "remote_only"}]})
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
    from libs.matching import matching_jobs

    db, user = account
    _search(db, user, {"sections": [{"platform": "all", "keywords": []}]})
    _jobs(db, ("Backend Engineer", "", True, 1))

    assert matching_jobs(db, user) == []


@needs_db
def test_only_a_person_with_a_healthy_proxy_of_their_own_sees_linkedin_jobs(account):
    """Decision D3: LinkedIn is fetched through someone's own proxy, never shared with the rest."""
    from libs.db import insert
    from libs.db.models import ProxyCredential
    from libs.matching import matching_jobs

    db, user = account
    linkedin, board = _jobs(db, ("Backend Engineer", "", True, 1), ("Backend Engineer", "", True, 2))
    db.corpus.update_one({"_id": linkedin.id}, {"$set": {"source": "linkedin"}})

    assert [m.job.id for m in matching_jobs(db, user)] == [board.id]

    insert(db, ProxyCredential(user_id=user.id, secret_ref=f"{user.id}:proxy", status="healthy"))
    assert {m.job.id for m in matching_jobs(db, user)} == {linkedin.id, board.id}
