"""The LinkedIn pass: every country somebody picked, read whole with no search word, ten jobs a page;
every job stored, whoever has a word for it. Words are matched on the stored jobs, never asked of LinkedIn
(owner, 2026-11-04). And the describer, which reads every stored job's text all the time (2026-11-05)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from libs.db import insert, lease
from libs.db.models import CorpusJob, SweptPlace, User, UserConfig
from services.harvester import describer, linkedin

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _config(*words: str, places=("germany",)) -> dict:
    return {"languages": ["en"],
            "sections": [{"platform": "all", "keywords": list(words), "locations": list(places)}]}


def _user(db, config: dict, **kwargs) -> User:
    kwargs = {"status": "active", "active": True, **kwargs}
    user = insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x",
                           subscription_status="active", **kwargs))
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
    """LinkedIn's guest list for each place, ten jobs a page, each job's own page, and every request. With
    `refuse_every`, the proxy in front of it cannot open every so many connections: one of its addresses
    answering 503 to every one, as two of six did on 2026-11-05."""

    def __init__(self, listed: dict[str, list[str]], *, refuse_once: set[int] = frozenset(),
                 refuse_every: int = 0):
        self.listed = listed
        self.refuse_once = set(refuse_once)
        self.refuse_every, self.connections = refuse_every, 0
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
        self.connections += 1
        # The first, and every `refuse_every`th after it: a list's first page refused ended the country with
        # no jobs, read to its end as far as anybody could tell.
        if self.refuse_every and self.connections % self.refuse_every == 1:
            raise httpx.ProxyError("503 Service Unavailable")
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
        monkeypatch.setattr(describer, "make_site_client", fake)
        return fake
    return install


def _pass(db, now=NOW):
    return linkedin.run_pass(db, now=now)


def _described(db, rounds=1) -> list[float]:
    """The describer's rounds, and the rests it took between them."""
    rests: list[float] = []
    describer.run(db, rounds=rounds, sleep=rests.append)
    return rests


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


def test_every_job_stored_is_described_by_the_describer_without_waiting_for_the_next_run(db, site):
    """A job's text is what its words are found in: a nurse job asking for Python is a Python job. The run
    only lists; the describer reads every text as soon as it can."""
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python", "golang", "sql"))

    _pass(db)
    assert linkedin_site.opened == [], "the run lists, and reads no job's text"
    _described(db)

    assert len(linkedin_site.opened) == 25
    assert db[CorpusJob.COLLECTION].count_documents({"description": {"$regex": "in Python"}}) == 25


def test_one_proxy_address_refusing_every_connection_stops_neither_the_lists_nor_the_texts(db, site):
    """Two of the proxy's six addresses answered 503 to every connection (2026-11-05). Taken as the end
    of a list, twelve countries were read again from hours back every run; taken as the proxy being
    unusable, it stopped every text for the run after about four. Each is asked again, from another."""
    site({"Germany": GERMANY}, refuse_every=3)
    _user(db, _config("python", "golang", "sql"))

    report = _pass(db)
    _described(db)

    assert report.new_jobs == 25
    assert db[SweptPlace.COLLECTION].count_documents({"_id": "Germany"}) == 1, "read to its end"
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
    monkeypatch.setattr(describer, "make_site_client", fake)
    _user(db, _config("python", "golang", "sql"))
    put(db, "proxy_exits", addresses)

    _pass(db)
    _described(db)

    assert len(fake.opened) == 25, "the premise: enough jobs to read more pages at once than that"
    assert fake.most == addresses


def test_a_job_is_stored_with_the_time_its_card_says_not_only_its_day(db, site):
    """LinkedIn's list dates a job with no time, and says "5 minutes ago": read as the job is found, that is
    to the minute. Stored as the day, every LinkedIn job read as posted at midnight until its own page was
    read, and an hour on, the page says only "1 hour ago"."""
    site({"Germany": ["Python Developer"]})
    _user(db, _config("python developer"))

    _pass(db)

    job = db[CorpusJob.COLLECTION].find_one({"source": "linkedin"})
    assert timedelta(minutes=5) <= job["first_seen_at"] - job["posted_at"] < timedelta(minutes=6)


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
    as one list, ~300 were lost every run. Germany picked as well is still read once. A job in each,
    since an empty first page is asked again (`test_a_refused_first_page_is_asked_again…`)."""
    from libs.corpus.geo import COUNTRY_LABELS, EU_COUNTRIES

    linkedin_site = site({COUNTRY_LABELS[code]: ["Nurse"] for code in (*EU_COUNTRIES, "GB")})
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

    seconds = [int(asked["f_TPR"][1:]) for asked in linkedin_site.asked]
    reach = [freshness_hours(db, "linkedin") * 3600, (15 + PLATFORM_TICK_MIN) * 60]
    assert [0 < got - want < 120 for got, want in zip(seconds, reach, strict=True)] == [True, True], (
        seconds, "and under two minutes more, a number LinkedIn has not been asked lately")
    assert {asked["sortBy"] for asked in linkedin_site.asked} == {"DD"}, "newest first"


def test_no_two_runs_ask_linkedin_for_the_same_window_nor_a_whole_number_of_minutes(db, site):
    """LinkedIn answers a list it was asked for lately from that answer: the same window asked again four
    minutes on gave the same page, the job listed between missing, and runs 15 minutes apart all asked
    r1800, which gave a copy 27 minutes old (2026-11-06). So none repeats for 59 runs, and none is round."""
    linkedin_site = site({"Germany": ["Nurse"]})
    _user(db, _config("nurse", "carer", "midwife"))

    for run in range(59):
        _pass(db, now=NOW + timedelta(minutes=15 * run))

    seconds = [int(asked["f_TPR"][1:]) for asked in linkedin_site.asked]
    assert len(set(seconds)) == len(seconds) == 59, "fourteen hours of runs, every window new"
    assert not [each for each in seconds if each % 60 == 0]
    assert all(0 < each - 1800 < 120 for each in seconds[1:]), "to the last run and one more, and under 2 min"


@pytest.mark.parametrize("refused", [0, 10], ids=["first page", "a later page"])
def test_a_country_whose_sweep_ended_on_refusals_is_read_further_back_next_time(db, site, monkeypatch,
                                                                                refused):
    """On its first page too: refused there, a country read as a window with nothing in it, its sweep counted
    as read to the end, and two such runs in a row lost the jobs listed between (2026-11-13)."""
    from libs.settings import PLATFORM_TICK_MIN

    linkedin_site = site({"Germany": ["Nurse"] * 15}, refuse_once={refused})
    _user(db, _config("nurse", "carer", "midwife"))
    _pass(db)
    linkedin_site.refuse_once = {refused}
    monkeypatch.setattr(linkedin, "EMPTY_RETRIES", 0)
    _pass(db, now=NOW + timedelta(minutes=15))
    linkedin_site.asked.clear()
    _pass(db, now=NOW + timedelta(minutes=30))

    assert 0 < int(linkedin_site.asked[0]["f_TPR"][1:]) - (30 + PLATFORM_TICK_MIN) * 60 < 120, (
        "back to the last sweep read to its end, not the one LinkedIn cut short")


def test_a_refused_first_page_is_asked_again_and_the_country_read_in_the_same_run(db, site):
    """LinkedIn refuses with the empty page that also means a window with no job in it. On the first page it
    was taken for that: Germany's run read nothing, and the run said nothing (2026-11-13)."""
    linkedin_site = site({"Germany": GERMANY}, refuse_once={0})
    _user(db, _config("python developer", "golang", "sql"))

    report = _pass(db)

    assert report.new_jobs == 25
    assert [asked["start"] for asked in linkedin_site.asked] == [0, 0, 10, 20]
    assert db[SweptPlace.COLLECTION].count_documents({"_id": "Germany"}) == 1, "read to its end"


def test_after_a_stoppage_a_country_is_read_back_to_its_last_full_read_as_far_as_a_job_is_shown(db, site):
    """A sweep reached back at most the operator's 4 hours, so a stoppage longer than that (the harvester
    down, the proxy out) lost every job listed before its last 4 hours, unsaid (2026-11-13). A job stays on
    Jobs pages for the job shelf life, so a sweep reaches back to its last full read, as far as that."""
    from libs.corpus.freshness import candidate_since, platform_windows
    from libs.settings import PLATFORM_TICK_MIN

    linkedin_site = site({"Germany": ["Nurse"]})
    _user(db, _config("nurse", "carer", "midwife"))
    later, much_later = NOW + timedelta(hours=10), NOW + timedelta(days=60)

    for when in (NOW, later, much_later):
        _pass(db, now=when)

    seconds = [int(asked["f_TPR"][1:]) for asked in linkedin_site.asked]
    windows = platform_windows(db)
    shelf = much_later - candidate_since(windows.max_age_hours, windows.source_min_ages, now=much_later)
    assert 0 < seconds[1] - (10 * 60 + PLATFORM_TICK_MIN) * 60 < 120, "ten hours back, and one run more"
    assert 0 < seconds[2] - shelf.total_seconds() < 120, "no further back than a job can still be shown"


def test_a_pass_out_of_time_stores_what_it_read_and_the_next_run_reads_the_rest(db, site, monkeypatch,
                                                                                caplog):
    """The run's unit is stopped at 14 minutes (`deploy/harvester.service`), and a sweep stores nothing until
    every country is read: a long one, after a stoppage, was stopped with nothing stored, and the next one,
    reaching further back, likewise. A pass stops reading when its time is spent and stores what it read."""
    clock, per_page = [0.0], [60.0]
    monkeypatch.setattr(linkedin, "monotonic", lambda: clock[0], raising=False)
    linkedin_site = site({"Germany": ["Nurse"] * 195})
    listing = linkedin_site.get

    def slow(url, params=None, **kwargs):
        clock[0] += per_page[0]
        return listing(url, params, **kwargs)

    monkeypatch.setattr(linkedin_site, "get", slow)
    _user(db, _config("nurse", "carer", "midwife"))

    with caplog.at_level(logging.WARNING, logger=linkedin.logger.name):
        late = _pass(db)
    read = linkedin.PASS_BUDGET_SECONDS // 60 * linkedin.SWEEP_PAGE
    assert (late.swept, late.new_jobs) == (read, read), "a minute a page, until the pass's time was spent"
    assert "ran out" in caplog.text
    assert db[SweptPlace.COLLECTION].count_documents({"_id": "Germany"}) == 0, "not read to its end"

    per_page[0] = 0
    _pass(db, now=NOW + timedelta(minutes=15))
    assert db[CorpusJob.COLLECTION].count_documents({"source": "linkedin"}) == 195
    assert db[SweptPlace.COLLECTION].count_documents({"_id": "Germany"}) == 1


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
    """The describer does not depend on the run: what is stored waits, and is read."""
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
    _described(db)

    assert "sweep" in report.failures
    assert [url.rsplit("/", 1)[1] for url in linkedin_site.opened] == ["42"]


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


# ── the describer, all the time (2026-11-05) ──────────────────────────────────────────────────

def test_a_sweep_running_goes_first(db, site):
    """The two never spend LinkedIn's allowance at once: while the run lists, the describer waits."""
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python", "golang", "sql"))
    _pass(db)

    with lease(db, "linkedin", 60):
        rests = _described(db)
    assert (linkedin_site.opened, rests) == ([], [describer.REST_SECONDS])

    _described(db)
    assert len(linkedin_site.opened) == 25


def test_the_describer_goes_straight_on_while_there_is_text_to_read_and_rests_when_there_is_none(db, site,
                                                                                                monkeypatch):
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python", "golang", "sql"))
    _pass(db)
    monkeypatch.setattr(describer, "ROUND", 10)

    rests = _described(db, rounds=4)

    assert len(linkedin_site.opened) == 25
    assert rests == [describer.REST_SECONDS], "three rounds read 10, 10 and 5; the fourth found nothing"


def test_a_round_that_fails_is_said_and_the_describer_goes_on(db, site, monkeypatch, caplog):
    site({"Germany": GERMANY})
    _user(db, _config("python", "golang", "sql"))

    def broken(*_args, **_kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(describer, "describe_pending", broken)
    with caplog.at_level(logging.ERROR):
        rests = _described(db, rounds=2)

    assert rests == [describer.REST_SECONDS] * 2
    assert caplog.text.count("describer: a round failed") == 2


def test_the_describer_says_what_it_read_once_a_tick_not_once_a_round(db, site, monkeypatch, caplog):
    site({"Germany": GERMANY}, refuse_every=3)
    _user(db, _config("python", "golang", "sql"))
    _pass(db)
    monkeypatch.setattr(describer, "PLATFORM_TICK_MIN", 0)

    with caplog.at_level(logging.INFO, logger=describer.logger.name):
        _described(db)

    lines = [record.getMessage() for record in caplog.records if record.name == describer.logger.name]
    assert len(lines) == 1
    assert lines[0].startswith("describer: 25 text(s) read of 25 tried in 1 round(s), 0 waiting")
    assert "'li_proxy_error'" in lines[0], "the refusals are counted, not hidden"


def test_without_the_proxy_or_with_linkedin_off_the_describer_reads_nothing(db, site, monkeypatch):
    linkedin_site = site({"Germany": GERMANY})
    _user(db, _config("python", "golang", "sql"))
    _pass(db)

    monkeypatch.setenv("HARVEST_LINKEDIN_DISABLED", "1")
    _described(db)
    monkeypatch.delenv("HARVEST_LINKEDIN_DISABLED")
    monkeypatch.delenv("HARVEST_PROXY")
    _described(db)

    assert linkedin_site.opened == []


def test_a_stored_account_with_its_retired_linkedin_schedule_still_loads_and_the_prune_removes_it(db):
    """Each account's own LinkedIn time is gone with its search: an account stored with it must load,
    or every page of the site fails for it."""
    from libs.db import ensure_schema, get

    user = _user(db, _config("nurse", "carer", "midwife"))
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"linkedin_next_run_at": NOW}})

    assert get(db, User, user.id).email == user.email
    ensure_schema(db, prune=True)
    assert "linkedin_next_run_at" not in db[User.COLLECTION].find_one({"_id": user.id})
