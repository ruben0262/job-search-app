"""The LinkedIn pass: every country somebody picked, read whole with no search word, ten jobs a page;
every job stored, whoever has a word for it, and described in the same pass. Words are matched on the
stored jobs, never asked of LinkedIn (owner, 2026-11-04)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from libs.db import insert, lease
from libs.db.models import CorpusJob, SweptPlace, User, UserConfig
from services.harvester import linkedin

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _config(*words: str, places=("germany",)) -> dict:
    return {"languages": ["en"],
            "sections": [{"platform": "all", "keywords": list(words), "locations": list(places)}]}


def _user(db, config: dict, **kwargs) -> User:
    kwargs = {"status": "active", "active": True, **kwargs}
    user = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x", **kwargs))
    insert(db, UserConfig(user_id=user.id, config=config))
    return user


def _card(job_id: int, title: str) -> str:
    """One job as LinkedIn's guest list writes it."""
    return (f'<li><div class="base-card base-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">'
            f'<h3 class="base-search-card__title">{title}</h3>'
            # A company each: the same title at the same company and place is one job to the corpus.
            f'<h4 class="base-search-card__subtitle">Company {job_id}</h4>'
            f'<span class="job-search-card__location">Berlin, Germany</span>'
            # Today: a job past the delivery window is never opened, its text would reach nobody.
            f'<time class="job-search-card__listdate" datetime="{datetime.now(UTC).date()}">5 minutes ago'
            '</time></div></li>')


class LinkedIn:
    """LinkedIn's guest list for each place, ten jobs a page, each job's own page, and every request."""

    def __init__(self, listed: dict[str, list[str]], *, refuse_once: set[int] = frozenset()):
        self.listed = listed
        self.refuse_once = set(refuse_once)
        self.asked: list[dict] = []
        self.opened: list[str] = []
        self.megabytes_downloaded = 0.0

    def __call__(self, _proxy):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url, params=None, **_kwargs):
        if url != linkedin.SWEEP_URL:
            self.opened.append(url)
            return SimpleNamespace(status_code=200, url=url, text='<div class="show-more-less-html__markup">'
                                   + "Build the payments API in Python. " * 20 + "</div>")
        self.asked.append(dict(params))
        start = params["start"]
        if start in self.refuse_once:
            self.refuse_once.discard(start)
            return SimpleNamespace(status_code=200, text="<!DOCTYPE html>\n\n<!---->")
        titles = self.listed.get(params["location"], [])
        # Ids apart for each place, as LinkedIn's are.
        offset = 1_000_000 * (1 + sorted(self.listed).index(params["location"])) if titles else 0
        page = titles[start:start + linkedin.SWEEP_PAGE]
        return SimpleNamespace(status_code=200, text="".join(
            _card(offset + start + n, title) for n, title in enumerate(page)))


@pytest.fixture()
def site(monkeypatch):
    monkeypatch.setenv("HARVEST_PROXY", "http://user:pass@proxy.example:8080")

    def install(listed, **kwargs) -> LinkedIn:
        fake = LinkedIn(listed, **kwargs)
        monkeypatch.setattr(linkedin, "make_site_client", fake)
        return fake
    return install


def _pass(db, now=NOW):
    return linkedin.run_pass(db, now=now)


GERMANY = ["Python Developer", "Nurse", "Senior Python Developer (m/w/d)", "Accountant"] * 6 + ["Welder"]


def test_every_job_a_country_lists_is_stored_whoever_has_a_word_for_it(db, site):
    """Words are matched on what is stored, title and text: a job nobody has a word for today is there
    for the word somebody adds tomorrow. Only titles with a word were kept before."""
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python developer", "golang", "sql"))

    report = _pass(db)

    stored = sorted(job["title"] for job in db[CorpusJob.COLLECTION].find({"source": "linkedin"}))
    assert stored == sorted(GERMANY)
    assert (report.swept, report.new_jobs) == (25, 25)
    assert len(linkedin_site.asked) == 3, "25 jobs are three pages, and a short page is the end"
    assert {asked["keywords"] for asked in linkedin_site.asked} == {""}, "no word is asked of LinkedIn"


def test_every_job_stored_is_described_in_the_same_pass(db, site):
    """A job's text is what its words are found in: a nurse job asking for Python is a Python job."""
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python", "golang", "sql"))

    report = _pass(db)

    assert report.described == 25 and len(linkedin_site.opened) == 25
    assert db[CorpusJob.COLLECTION].count_documents({"description": {"$regex": "in Python"}}) == 25


@pytest.mark.parametrize("addresses", [2, 5])
def test_linkedin_is_read_a_page_at_once_from_each_of_the_proxys_addresses(db, site, monkeypatch, addresses):
    """The proxy sends each new connection from its next address, and LinkedIn's limit is a rate per
    address, so a pass reads as many pages at once as the proxy has addresses (Admin → Settings). It read
    four whatever the proxy had, and the proxy went from four to six on 2026-11-05."""
    import threading
    import time

    from libs.settings import put

    class AtOnce(LinkedIn):
        """LinkedIn, counting the most pages it was asked for at the same time."""

        def __init__(self, listed):
            super().__init__(listed)
            self.lock, self.now, self.most = threading.Lock(), 0, 0

        def get(self, url, params=None, **kwargs):
            with self.lock:
                self.now += 1
                self.most = max(self.most, self.now)
            time.sleep(0.02)
            try:
                return super().get(url, params, **kwargs)
            finally:
                with self.lock:
                    self.now -= 1

    site({})  # the proxy's address, set
    fake = AtOnce({"Germany": GERMANY})
    monkeypatch.setattr(linkedin, "make_site_client", fake)
    _user(db, _config("python", "golang", "sql"))
    put(db, "proxy_exits", addresses)

    report = _pass(db)

    assert report.described == 25, "the premise: enough jobs to read more pages at once than that"
    assert fake.most == addresses


def test_a_pass_costs_the_same_however_many_people_and_words_there_are(db, site):
    few = site({"Germany": GERMANY})
    _user(db, _config("python developer", "golang", "sql"))
    _pass(db)

    db[SweptPlace.COLLECTION].delete_many({})
    many = site({"Germany": GERMANY})
    for n in range(5):
        _user(db, _config(*[f"word{n}-{m}" for m in range(25)]))
    _pass(db)

    assert len(many.asked) == len(few.asked) == 3


def test_a_region_is_read_as_its_countries_each_once(db, site):
    """LinkedIn lists no further than 1,000 jobs, and the EU's 30 minutes held ~1,300 (2026-11-04): read
    as one list, its oldest were lost every run. Germany picked as well is still read once."""
    from libs.corpus.geo import EU_COUNTRIES

    linkedin_site = site({"Germany": ["Nurse"], "France": ["Nurse"], "United Kingdom": ["Nurse"]})
    _user(db, _config("nurse", "carer", "midwife", places=("germany",)))
    _user(db, _config("nurse", "carer", "midwife", places=("europe", "united_kingdom")))

    _pass(db)

    asked = [asked["location"] for asked in linkedin_site.asked if asked["start"] == 0]
    assert len(asked) == len(set(asked)) == len(EU_COUNTRIES) + 1, "the 27 members, and the UK"
    assert "European Union" not in asked and {"Germany", "France", "United Kingdom"} <= set(asked)


def test_only_active_peoples_countries_are_read(db, site):
    linkedin_site = site({"Germany": GERMANY, "Ireland": ["Nurse"]})
    _user(db, _config("nurse", "carer", "midwife", places=("ireland",)), active=False)

    _pass(db)

    assert linkedin_site.asked == []


def test_an_empty_page_after_a_full_one_is_asked_again_before_it_counts_as_the_end(db, site):
    """LinkedIn answers a refusal with the empty page it ends its list with: a sweep of Germany's hour
    stopped at 20 of ~300 jobs that way (2026-11-03)."""
    linkedin_site = site({"Germany": GERMANY}, refuse_once={10})
    _user(db, _config("welder", "golang", "sql"))

    report = _pass(db)

    assert report.swept == 25
    assert db[CorpusJob.COLLECTION].count_documents({"title": "Welder"}) == 1, "the last page was still read"
    assert [asked["start"] for asked in linkedin_site.asked] == [0, 10, 10, 20]


def test_each_country_is_read_back_to_its_previous_sweep_and_one_run_more(db, site):
    """A job listed at the edge of one window is inside the next: a sweep missed 8 of 20 jobs listed
    exactly an hour before (2026-11-03). A country nobody has read yet reaches back the operator's window."""
    from libs.settings import PLATFORM_TICK_MIN, freshness_hours

    linkedin_site = site({"Germany": ["Nurse"]})
    _user(db, _config("nurse", "carer", "midwife"))

    _pass(db)
    _pass(db, now=NOW + timedelta(minutes=15))

    windows = [asked["f_TPR"] for asked in linkedin_site.asked]
    assert windows == [f"r{freshness_hours(db, 'linkedin') * 3600}", f"r{(15 + PLATFORM_TICK_MIN) * 60}"]
    assert {asked["sortBy"] for asked in linkedin_site.asked} == {"DD"}, "newest first"


def test_a_country_whose_sweep_ended_on_refusals_is_read_further_back_next_time(db, site):
    from libs.settings import PLATFORM_TICK_MIN

    linkedin_site = site({"Germany": ["Nurse"] * 15}, refuse_once={10})
    _user(db, _config("nurse", "carer", "midwife"))
    _pass(db)
    linkedin_site.refuse_once = {10}
    linkedin.EMPTY_RETRIES, retries = 0, linkedin.EMPTY_RETRIES
    try:
        _pass(db, now=NOW + timedelta(minutes=15))
        linkedin_site.asked.clear()
        _pass(db, now=NOW + timedelta(minutes=30))
    finally:
        linkedin.EMPTY_RETRIES = retries

    assert linkedin_site.asked[0]["f_TPR"] == f"r{(30 + PLATFORM_TICK_MIN) * 60}", (
        "back to the last sweep read to its end, not the one LinkedIn cut short")


def test_a_country_listing_more_than_linkedin_shows_says_so(db, site, caplog, monkeypatch):
    """LinkedIn lists no further than its 1,000th job; thirty here, so the test reads three pages."""
    monkeypatch.setattr(linkedin, "SWEEP_DEPTH", 30)
    linkedin_site = site({"Germany": ["Engineer"] * 35})
    _user(db, _config("python developer", "golang", "sql"))

    with caplog.at_level(logging.WARNING, logger=linkedin.logger.name):
        _pass(db)

    assert len(linkedin_site.asked) == linkedin.SWEEP_DEPTH // linkedin.SWEEP_PAGE
    assert "reached LinkedIn's 1,000th job" in caplog.text


def test_a_sweep_that_fails_is_reported_and_what_earlier_passes_stored_is_still_described(db, site,
                                                                                        monkeypatch):
    from libs.corpus.repository import CorpusRepository
    from services.harvester.sources.base import JobLink

    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python developer", "golang", "sql"))
    CorpusRepository(db).add(**JobLink(url="https://www.linkedin.com/jobs/view/42", title="Nurse",
                                       company="Mercy", location="Berlin", source="linkedin").as_corpus_row())

    def broken(*_args, **_kwargs):
        raise RuntimeError("LinkedIn changed its page")

    monkeypatch.setattr(linkedin, "sweep_places", broken)
    report = _pass(db)

    assert "sweep" in report.failures
    assert report.described == 1 and [url.rsplit("/", 1)[1] for url in linkedin_site.opened] == ["42"]


def test_two_passes_never_read_linkedin_at_once(db, site):
    """A pass still describing when the next tick starts: the second would spend LinkedIn's allowance
    on the same jobs."""
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python developer", "golang", "sql"))

    with lease(db, "linkedin", 60) as held:
        assert held
        report = _pass(db)

    assert report.skipped_locked and linkedin_site.asked == linkedin_site.opened == []


def test_without_the_site_proxy_linkedin_is_not_read_and_it_is_said(db, site, monkeypatch, caplog):
    linkedin_site = site({"Germany": GERMANY})
    monkeypatch.delenv("HARVEST_PROXY")
    _user(db, _config("python developer", "golang", "sql"))

    with caplog.at_level(logging.WARNING):
        report = _pass(db)

    assert (report.swept_places, linkedin_site.asked) == (0, [])
    assert "HARVEST_PROXY is not set" in caplog.text


def test_the_tier_can_be_switched_off_for_everybody(db, site, monkeypatch):
    """The alternative during an incident is editing every person's search while LinkedIn refuses."""
    linkedin_site = site({"Germany": GERMANY})
    monkeypatch.setenv("HARVEST_LINKEDIN_DISABLED", "1")
    _user(db, _config("python developer", "golang", "sql"))

    _pass(db)

    assert linkedin_site.asked == []


def test_a_stored_account_with_its_retired_linkedin_schedule_still_loads_and_the_prune_removes_it(db):
    """Each account's own LinkedIn time is gone with its search: an account stored with it must load,
    or every page of the site fails for it."""
    from libs.db import ensure_schema, get

    user = _user(db, _config("nurse", "carer", "midwife"))
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"linkedin_next_run_at": NOW}})

    assert get(db, User, user.id).email == user.email
    ensure_schema(db, prune=True)
    assert "linkedin_next_run_at" not in db[User.COLLECTION].find_one({"_id": user.id})
