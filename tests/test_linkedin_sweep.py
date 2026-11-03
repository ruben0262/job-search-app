"""The LinkedIn sweep: each place's newest jobs read with no search word, ten a page, and the ones whose
title has somebody's word stored, however many words there are."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from libs.db import insert
from libs.db.models import CorpusJob, User, UserConfig
from services.harvester import linkedin

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _config(*words: str, places=("germany",)) -> dict:
    return {"sections": [{"platform": "all", "keywords": list(words), "locations": list(places)}]}


def _user(db, config: dict, **kwargs) -> User:
    user = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", status="active",
                           active=True, **kwargs))
    insert(db, UserConfig(user_id=user.id, config=config))
    return user


def _card(job_id: int, title: str) -> str:
    """One job as LinkedIn's guest list writes it."""
    return (f'<li><div class="base-card base-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">'
            f'<h3 class="base-search-card__title">{title}</h3>'
            # A company each: the same title at the same company and place is one job to the corpus.
            f'<h4 class="base-search-card__subtitle">Company {job_id}</h4>'
            f'<span class="job-search-card__location">Berlin, Germany</span>'
            f'<time class="job-search-card__listdate" datetime="2026-07-31">5 minutes ago</time></div></li>')


class LinkedIn:
    """LinkedIn's guest list for each place, ten jobs a page, and every request it was asked."""

    def __init__(self, listed: dict[str, list[str]], *, refuse_once: set[int] = frozenset()):
        self.listed = listed
        self.refuse_once = set(refuse_once)
        self.asked: list[dict] = []
        self.megabytes_downloaded = 0.0

    def __call__(self, _proxy):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url, params=None, **_kwargs):
        if url != linkedin.SWEEP_URL:
            # Describing what was stored: a job page with its text.
            return SimpleNamespace(status_code=200, url=url, text='<div class="show-more-less-html__markup">'
                                   + "Build the payments API. " * 20 + "</div>")
        self.asked.append(dict(params))
        start = params["start"]
        if start in self.refuse_once:
            self.refuse_once.discard(start)
            return SimpleNamespace(status_code=200, text="<!DOCTYPE html>\n\n<!---->")
        titles = self.listed.get(params["location"], [])
        page = titles[start:start + linkedin.SWEEP_PAGE]
        return SimpleNamespace(status_code=200, text="".join(
            _card(1_000_000 + start + n, title) for n, title in enumerate(page)))


@pytest.fixture()
def site(monkeypatch):
    monkeypatch.setenv("HARVEST_PROXY", "http://user:pass@proxy.example:8080")

    def install(listed, **kwargs) -> LinkedIn:
        fake = LinkedIn(listed, **kwargs)
        monkeypatch.setattr(linkedin, "make_site_client", fake)
        return fake
    return install


def _pass(db, gather=None):
    return linkedin.run_for_due_users(db, now=NOW, gather=gather or (lambda *_a, **_k: []))


GERMANY = ["Python Developer", "Nurse", "Senior Python Developer (m/w/d)", "Accountant"] * 6 + ["Welder"]


def test_a_job_whose_title_has_somebodys_word_is_stored_with_no_word_search(db, site):
    """The word searches here found nothing; the sweep still finds every Python developer listed, and
    stores none of the jobs nobody has a word for."""
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python developer"))

    report = _pass(db)

    stored = sorted(job["title"] for job in db[CorpusJob.COLLECTION].find({"source": "linkedin"}))
    assert stored == sorted(title for title in GERMANY if "Python Developer" in title)
    assert (report.swept, report.sweep_kept) == (25, 12)
    assert len(linkedin_site.asked) == 3, "25 jobs are three pages, and a short page is the end"


def test_a_sweep_costs_the_same_however_many_words_people_have(db, site):
    few = site({"Germany": GERMANY})
    _user(db, _config("python developer"))
    _pass(db)

    db[User.COLLECTION].update_many({}, {"$set": {"linkedin_next_run_at": None}})
    many = site({"Germany": GERMANY})
    _user(db, _config(*[f"word{n}" for n in range(24)], "nurse"))
    _pass(db)

    assert len(many.asked) == len(few.asked) == 3
    assert db[CorpusJob.COLLECTION].count_documents({"title": "Nurse"}) == 6, "a new word costs no request"


def test_an_empty_page_after_a_full_one_is_asked_again_before_it_counts_as_the_end(db, site):
    """LinkedIn answers a refusal with the empty page it ends its list with: a sweep of Germany's hour
    stopped at 20 of ~300 jobs that way (2026-11-03)."""
    linkedin_site = site({"Germany": GERMANY}, refuse_once={10})
    _user(db, _config("welder"))

    report = _pass(db)

    assert report.swept == 25
    assert db[CorpusJob.COLLECTION].count_documents({"title": "Welder"}) == 1, "the last page was still read"
    assert [asked["start"] for asked in linkedin_site.asked] == [0, 10, 10, 20]


def test_a_job_the_word_search_also_found_is_one_job(db, site):
    from services.harvester.sources.base import JobLink

    site({"Germany": ["Python Developer"]})
    _user(db, _config("python developer"))
    searched = JobLink(url="https://www.linkedin.com/jobs/view/1000000", title="Python Developer",
                       company="Acme", location="Berlin, Germany", source="linkedin")

    _pass(db, gather=lambda *_a, **_k: [searched])

    assert db[CorpusJob.COLLECTION].count_documents({"title": "Python Developer"}) == 1


def test_the_sweep_reaches_back_to_the_previous_pass_and_one_run_more(db, site):
    """A job listed at the edge of one window is inside the next: a sweep missed 8 of 20 jobs listed
    exactly an hour before (2026-11-03). A person never searched reaches back the operator's window."""
    from libs.settings import PLATFORM_TICK_MIN, freshness_hours

    linkedin_site = site({"Germany": ["Python Developer"], "Ireland": ["Python Developer"]})
    _user(db, _config("python developer"), linkedin_next_run_at=NOW)
    _user(db, _config("python developer", places=("ireland",)), linkedin_next_run_at=None)

    _pass(db)

    windows = {asked["location"]: asked["f_TPR"] for asked in linkedin_site.asked}
    assert windows["Germany"] == f"r{(15 + PLATFORM_TICK_MIN) * 60}", "on time: its interval and a run"
    assert windows["Ireland"] == f"r{freshness_hours(db, 'linkedin') * 3600}"
    assert {asked["sortBy"] for asked in linkedin_site.asked} == {"DD"}, "newest first"


def test_a_person_with_no_place_is_not_swept(db, site):
    """Every place's jobs at once is more than a list can be read to; their word searches still run."""
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python developer", places=()))

    report = _pass(db)

    assert (linkedin_site.asked, report.swept_places) == ([], 0)


def test_a_place_listing_more_than_linkedin_shows_says_so(db, site, caplog):
    linkedin_site = site({"Germany": ["Engineer"] * 1005})
    _user(db, _config("python developer"))

    with caplog.at_level(logging.WARNING, logger=linkedin.logger.name):
        _pass(db)

    assert len(linkedin_site.asked) == linkedin.SWEEP_DEPTH // linkedin.SWEEP_PAGE
    assert "reached LinkedIn's 1,000th job" in caplog.text


def test_a_sweep_that_fails_is_reported_and_the_pass_goes_on(db, site, monkeypatch):
    site({"Germany": GERMANY})
    _user(db, _config("python developer"))

    def broken(*_args, **_kwargs):
        raise RuntimeError("LinkedIn changed its page")

    monkeypatch.setattr(linkedin, "sweep_places", broken)
    report = _pass(db)

    assert "sweep" in report.failures
    assert report.considered == 1


def test_people_due_later_are_not_swept_for_yet(db, site):
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python developer"), linkedin_next_run_at=NOW + timedelta(minutes=5))

    _pass(db)

    assert linkedin_site.asked == []
