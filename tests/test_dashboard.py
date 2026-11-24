"""The front page and the jobs page, both on `libs.matching`: what a person sees, and why."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta

import pytest

from libs.db import insert, now
from libs.db.models import CorpusJob, User, UserJobState
from libs.db.models.tenant import record_state
from tests.conftest import agreed

pytest.importorskip("fastapi")

# A stored config as accounts actually have them, retired field and all.
CONFIG = {"sections": [{"platform": "all", "keywords": ["backend engineer", "golang", "postgres"],
                        "locations": ["germany"]}],
          "languages": ["en"], "max_notifications_per_day": 20}


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.db.models import UserConfig
    from services.api.app import app
    from tests.conftest import access_cookie

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                           subscription_status="active", status="active",
                           active=True, full_name="Alice Example", telegram_status="connected",
                           telegram_chat_id="1", onboarding_completed_at=now(), **agreed()))
    insert(db, UserConfig(user_id=user.id, config=CONFIG, schema_version=2))

    jobs = [insert(db, CorpusJob(described_at=now(), url=f"https://example.com/{uuid.uuid4()}", source="test",
                                 title=title, description="we build things", is_remote=True))
            for title in ("Backend Engineer", "Senior Backend Engineer", "Registered Nurse")]

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", access_cookie(db, user.id), path="/")
        yield http, db, user, jobs


def _ids(body: dict) -> list[int]:
    return [job["id"] for job in body["jobs"]]


def _change(db, user, **fields) -> None:
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": fields})


def _page(body: dict) -> tuple[list[int], int]:
    """A list's page and its count, without what its filters offer."""
    return _ids(body), body["total"]


# ── the front page ────────────────────────────────────────────────────────────────────────────

def test_an_unfinished_signup_gets_the_onboarding_state_and_nothing_else(api):
    """Empty panels read as a broken product rather than an unfinished signup."""
    http, db, user, _ = api
    _change(db, user, onboarding_completed_at=None)

    body = http.get("/dashboard").json()
    assert body["stage"] == "onboarding"
    assert body["onboarding"], "the onboarding state is passed through for the signup screens"
    assert "matching" not in body


def test_the_front_page_counts_applications_and_never_runs_the_match(api, monkeypatch):
    """The jobs page counts its own list, so the dashboard every page waits for must not run a second
    match: that was 150 ms of a 13 ms answer (median, 2,000 rows)."""
    http, db, user, jobs = api
    insert(db, UserJobState(user_id=user.id, job_id=jobs[0].id, applied_at=now()))

    def no_match(*_args, **_kwargs):
        raise AssertionError("the dashboard ran the match")

    monkeypatch.setattr("libs.matching.matching_jobs", no_match)
    front = http.get("/dashboard").json()
    assert front["stage"] == "ready"
    assert front["applied"] == 1


def test_a_stopped_telegram_is_a_reconnect_offer_not_an_error(api):
    """Their old per-user bot, or they blocked the platform's: the page offers to reconnect."""
    http, db, user, _ = api
    _change(db, user, telegram_status="broken")

    banners = http.get("/dashboard").json()["banners"]
    assert [banner["key"] for banner in banners] == ["telegram_broken"]
    assert banners[0]["level"] != "error", "nothing is broken that the person cannot mend in one tap"


def test_alerts_that_reach_only_the_bell_are_not_nagged_about(api):
    """No Telegram, no phone, no email: their alerts still go to the bell, so they are on, and the page does
    not ask to turn them on (owner, 2026-11-18: "once alerts are on there is no need for these"). Setting a
    phone up is the Alerts page's, and a phone's own "Get the app"."""
    http, db, user, _ = api
    _change(db, user, telegram_status="unconfigured", telegram_chat_id=None)
    assert http.get("/dashboard").json()["banners"] == []


def test_the_front_page_says_how_much_about_you_says_against_what_is_enough(api):
    """Owner, 2026-11-22: About you empty, or 150 words or fewer, and the site asks for more until it has
    more: the menu's dot and the bell are drawn from what every page already reads, the words saved."""
    from libs.documents import about

    http, _db, _user, _ = api
    said = {"enough": about.ENOUGH_WORDS, "most": about.MOST_WORDS}
    assert http.get("/dashboard").json()["about"] == {"words": 0, **said}, "none written"
    for words in (about.ENOUGH_WORDS, about.ENOUGH_WORDS + 1):
        assert http.put("/onboarding/history", json={"text": "forklift " * words}).status_code == 200
        assert http.get("/dashboard").json()["about"] == {"words": words, **said}


def test_the_dashboard_requires_signing_in(api):
    http, _, _, _ = api
    http.cookies.clear()
    assert http.get("/dashboard").status_code == 401


# ── the jobs page ─────────────────────────────────────────────────────────────────────────────

def test_green_words_pick_the_jobs_and_each_says_why(api):
    """The question a person has about every row: why am I seeing this?"""
    http, _, _, jobs = api
    body = http.get("/feed").json()

    assert set(_ids(body)) == {jobs[0].id, jobs[1].id}, "the nurse has none of the green words"
    for job in body["jobs"]:
        assert job["words"] == ["backend engineer"]


def test_a_red_word_takes_a_job_off_the_page(api):
    from libs.db.models import UserConfig

    http, db, user, jobs = api
    db[UserConfig.COLLECTION].update_one({"user_id": user.id}, {"$set": {"config": {"sections": [{
        "platform": "all", "keywords": ["backend engineer"], "exclude_keywords": ["senior"]}]}}})

    assert _ids(http.get("/feed").json()) == [jobs[0].id]


def test_a_dismissed_job_stays_off_the_page(api):
    """Dismissing has to mean something, or the button is a lie."""
    http, db, user, jobs = api
    insert(db, UserJobState(user_id=user.id, job_id=jobs[0].id, dismissed_at=now()))

    body = http.get("/feed").json()
    assert _ids(body) == [jobs[1].id]
    assert body["total"] == 1


def test_the_feed_does_not_carry_the_full_description(api):
    """Up to 12,000 characters a job, and the list does not show it."""
    http, _, _, _ = api
    assert "description" not in http.get("/feed").json()["jobs"][0]


def test_the_feed_pages_and_reports_the_total_behind_the_page(api):
    """The pager needs the count under the same filters as the page, or "1 of 3" lies."""
    http, _, _, _ = api
    body = http.get("/feed?limit=1&offset=1").json()
    assert body["total"] == 2
    assert len(body["jobs"]) == 1


def test_the_search_box_reaches_titles_and_links(api):
    http, _, _, jobs = api
    assert _ids(http.get("/feed?q=senior").json()) == [jobs[1].id]
    # And the URL, because a pasted link is how people look a job back up.
    assert _ids(http.get(f"/feed?q={jobs[0].url.rsplit('/', 1)[1][:12]}").json()) == [jobs[0].id]


def test_remote_only_narrows_the_matches_and_unknown_is_not_remote(api):
    http, db, _, jobs = api
    unknown = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                   title="Backend Engineer II", description="x", is_remote=None,
                                   described_at=now()))

    assert unknown.id in _ids(http.get("/feed").json())
    assert set(_ids(http.get("/feed?remote=true").json())) == {jobs[0].id, jobs[1].id}


def test_a_salary_in_the_description_reaches_the_row_without_the_description(api):
    """The one thing the description holds that the table wants, extracted server-side for free."""
    http, db, _, jobs = api
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id}, {"$set": {
        "description": "Backend role paying €60,000 - €80,000 per year. We build things."}})

    row = next(job for job in http.get("/feed").json()["jobs"] if job["id"] == jobs[0].id)
    assert row["salary"]["min"] == 60000
    assert row["salary"]["max"] == 80000
    assert row["salary"]["currency"] == "EUR"
    assert "description" not in row


def test_equal_scores_are_newest_first(api):
    """Freshness breaks a tie: the order `libs.matching` gives, not a second one made up here."""
    http, db, _, _ = api
    moment = now()
    rows = {age: insert(db, CorpusJob(url=f"https://example.test/tie-{age}", source="test",
                                      title="Backend Engineer", description="x", is_remote=True,
                                      first_seen_at=moment - timedelta(hours=age), described_at=now()))
            # The older one first, so its id is the lower: insertion order cannot pass for freshness.
            for age in (5, 1)}

    order = [i for i in _ids(http.get("/feed").json()) if i in {r.id for r in rows.values()}]
    assert order == [rows[1].id, rows[5].id]


def test_posted_lately_keeps_only_jobs_from_the_last_hours_asked(api):
    """By the posting date where the board gives one, else when it was found. Within the freshness
    window every board already has: this narrows what is shown, it never brings back older jobs."""
    http, db, _, jobs = api
    found_earlier = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                         described_at=now(), title="Backend Engineer", description="x",
                                         first_seen_at=now() - timedelta(hours=4)))
    posted_earlier = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                          title="Backend Engineer", description="x", first_seen_at=now(),
                                          posted_at=now() - timedelta(hours=4), described_at=now()))
    earlier = {found_earlier.id, posted_earlier.id}

    assert earlier <= set(_ids(http.get("/feed").json())), "the premise: both are theirs"
    shown = set(_ids(http.get("/feed?since_hours=2").json()))
    assert {jobs[0].id, jobs[1].id} <= shown
    assert not shown & earlier


def test_one_board_at_a_time_and_every_board_is_offered_with_its_count(api):
    http, db, _, _jobs = api
    other = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="smartrecruiters",
                                 described_at=now(), title="Backend Engineer", description="x"))

    body = http.get("/feed?source=smartrecruiters").json()

    assert _ids(body) == [other.id]
    assert {each["name"]: (each["label"], each["jobs"]) for each in body["sources"]} == {
        "test": ("Test", 2), "smartrecruiters": ("SmartRecruiters", 1)}, (
        "every board by the name people write, counted before the filter, so another can be picked")
    assert body["jobs"][0]["apply_on"] == "SmartRecruiters", "what its button says: where to apply"


def test_no_document_yet_leaves_out_the_ones_with_a_cv_made(api):
    from libs.db.models import BuiltDocument

    http, db, user, jobs = api
    insert(db, BuiltDocument(id=999, user_id=user.id, job_id=jobs[1].id, kind="cv", mode="instant",
                             filename="cv.pdf", content=b"%PDF", size=4, saved=True))

    assert _ids(http.get("/feed?made=false").json()) == [jobs[0].id]
    assert _ids(http.get("/feed?made=true").json()) == [jobs[1].id]


def _text(db, job, text: str) -> None:
    db[CorpusJob.COLLECTION].update_one({"_id": job.id}, {"$set": {"description": text}})


def _words(db, user, words: list[str]) -> None:
    from libs.db.models import UserConfig

    db[UserConfig.COLLECTION].update_one({"user_id": user.id}, {"$set": {"config": {"sections": [{
        "platform": "all", "keywords": words}]}}})


def test_picked_words_keep_the_jobs_that_have_every_one_and_each_word_says_how_many(api):
    """A filter on "why": each word picked narrows the list to jobs that have all of them."""
    http, db, user, jobs = api
    _words(db, user, ["backend engineer", "python", "postgres"])
    _text(db, jobs[0], "Python and Postgres")
    _text(db, jobs[1], "Python")

    body = http.get("/feed").json()
    assert {each["name"]: each["jobs"] for each in body["words"]} == {
        "backend engineer": 2, "python": 2, "postgres": 1}
    assert _ids(http.get("/feed?word=python").json()) == [jobs[0].id, jobs[1].id]
    assert _ids(http.get("/feed?word=Python&word=postgres").json()) == [jobs[0].id]
    assert _ids(http.get("/feed?word=postgres").json()) == [jobs[0].id]


def test_at_least_so_many_words_keeps_the_jobs_with_that_many(api):
    http, db, user, jobs = api
    _words(db, user, ["backend engineer", "python", "postgres"])
    _text(db, jobs[0], "Python and Postgres")
    _text(db, jobs[1], "Python")

    assert _ids(http.get("/feed?min_words=3").json()) == [jobs[0].id]
    assert _ids(http.get("/feed?min_words=2").json()) == [jobs[0].id, jobs[1].id]
    assert _ids(http.get("/feed").json())[0] == jobs[0].id, "and the most words are first"


def test_a_job_whose_text_was_read_after_a_list_was_read_is_counted_as_new(api):
    """What the "new jobs" button counts, without reloading the list under the person reading it. A job
    reaches the list when its text is read: a LinkedIn job found before the list was read and read after
    it is new to them, and one found after it is not on the list until it is read."""
    from libs.corpus.repository import CorpusRepository

    http, db, _, _ = api
    corpus = CorpusRepository(db)
    found_before = corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="linkedin",
                              title="Backend Engineer")
    first = http.get("/feed").json()
    assert first["arrived"] is None, "not asked"
    as_of = first["as_of"]
    assert http.get("/feed", params={"found_after": as_of}).json()["arrived"] == 0

    found_after = corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="linkedin",
                             title="Senior Backend Engineer")
    assert http.get("/feed", params={"found_after": as_of}).json()["arrived"] == 0, "found, not read yet"

    corpus.set_description(found_before, "We build backend systems in Go. " * 10)
    nurse = corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="linkedin", title="Registered Nurse")
    corpus.set_description(nurse, "A ward in Berlin. " * 20)

    new = http.get("/feed", params={"found_after": as_of}).json()
    assert new["arrived"] == 1, "read after the list was, and theirs; the nurse is not"
    assert found_before in _ids(new) and found_after not in _ids(new)


def test_the_new_jobs_count_is_what_a_refresh_adds_less_the_jobs_that_left_meanwhile(api):
    """"100 new jobs", and a refresh made the list 15 longer (owner, 2026-11-06): the others had passed
    their shelf life meanwhile. Every job counted new is theirs and on the list a refresh brings, and the
    list's own length beside it says how many left."""
    from libs.corpus.repository import CorpusRepository

    http, db, _, jobs = api
    corpus = CorpusRepository(db)
    first = http.get("/feed").json()
    assert first["total"] == 2
    for title in ("Golang Developer", "Postgres Administrator", "Registered Nurse"):
        corpus.set_description(corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="linkedin",
                                          title=title), "We build backend systems in Go. " * 10)
    # Past the shelf life since the list was read.
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id},
                                        {"$set": {"first_seen_at": now() - timedelta(days=30)}})

    poll = http.get("/feed", params={"found_after": first["as_of"], "counts": True}).json()
    after = http.get("/feed").json()

    assert poll["arrived"] == 2, "theirs alone: the nurse has none of their words"
    assert poll["total"] == after["total"] == first["total"] + 2 - 1, "what a refresh shows, the one gone"
    assert jobs[0].id not in _ids(after)


def test_a_page_asks_for_the_account_its_plan_and_the_platforms_windows_once_each(api):
    """**A relationship, not a stopwatch.** The Jobs page asked what plan the account is on three times over
    and the platform's windows twice, and the front page read the account twice and its plan three times: 18
    and 9 questions for what 13 and 6 answer (2026-11-20). However a page is built, the account is read once,
    its plan once (two questions, a member and which), and the windows once (two settings)."""
    from collections import Counter

    from pymongo import MongoClient, monitoring

    from services.api.app import app
    from services.api.deps import get_db

    http, db, _user, _jobs = api
    asked: Counter = Counter()

    class Reads(monitoring.CommandListener):
        def started(self, event):
            if event.command_name in ("find", "aggregate", "count", "distinct"):
                asked[str(event.command.get(event.command_name))] += 1

        def succeeded(self, event): ...

        def failed(self, event): ...

    # A client of its own, made with the counter: a listener registered later never sees an existing one.
    counted = MongoClient(os.environ["MONGODB_URI"], uuidRepresentation="standard", tz_aware=True,
                          event_listeners=[Reads()])[db.name]
    app.dependency_overrides[get_db] = lambda: counted
    try:
        assert http.get("/feed").status_code == 200
        feed = Counter(asked)
        asked.clear()
        assert http.get("/dashboard").status_code == 200
        front = Counter(asked)
    finally:
        app.dependency_overrides.pop(get_db)
        counted.client.close()

    assert feed["corpus"] > 0, "the premise: the counter sees the page's questions"
    assert (feed["users"], feed["platform_settings"]) == (3, 2)
    assert front["users"] == 3


def test_the_minutes_count_of_new_jobs_brings_the_numbers_and_no_job(api, monkeypatch):
    """Every open Jobs page asks once a minute how many jobs are new. It was asked as a page of one job: the
    job read with its text and documents, every job of the list scored, and each filter's counts made, to
    read two numbers (2026-11-20). Asked for the numbers, nothing else is worked out."""
    http, _db, _user, _jobs = api
    first = http.get("/feed").json()

    def a_card(*_args, **_kwargs):
        raise AssertionError("a job was read to count the list")

    monkeypatch.setattr("services.api.dashboard_routes.feed_row", a_card)
    poll = http.get("/feed", params={"found_after": first["as_of"], "counts": True}).json()

    assert (poll["total"], poll["arrived"], poll["jobs"]) == (first["total"], 0, [])
    assert (poll["sources"], poll["words"], poll["countries"]) == ([], [], [])


def test_the_page_says_how_long_a_job_stays_and_shows_exactly_that(api):
    """A job leaving the list is no surprise when the page says how long jobs stay (owner, 2026-11-06). The
    numbers are Admin's job shelf life, thirty days unless Admin says otherwise (owner, 2026-11-12), and the
    longest a slower board keeps its jobs, and the jobs shown are the ones they describe: LinkedIn's for
    the shelf life, Workday's for a week at least."""
    from libs import settings

    http, db, _, _ = api
    found = {(source, hours): insert(db, CorpusJob(
        url=f"https://example.com/{uuid.uuid4()}", source=source, title="Backend Engineer", description="x",
        first_seen_at=now() - timedelta(hours=hours), described_at=now() - timedelta(hours=hours))).id
        for source, hours in (("linkedin", 23), ("linkedin", 25), ("workday", 160), ("workday", 170),
                              ("linkedin", 47), ("linkedin", 700), ("linkedin", 730))}

    def shown() -> tuple[tuple[int, int], set]:
        body = http.get("/feed").json()
        return (body["shelf_hours"], body["shelf_longest_hours"]), {
            key for key, job_id in found.items() if job_id in _ids(body)}

    assert shown() == ((720, 720), {("linkedin", 23), ("linkedin", 25), ("linkedin", 47), ("workday", 160),
                                    ("workday", 170), ("linkedin", 700)})
    settings.put(db, "job_max_age_hours", 24)
    assert shown() == ((24, 168), {("linkedin", 23), ("workday", 160)})
    settings.put(db, "job_max_age_hours", 48)
    assert shown() == ((48, 168), {("linkedin", 23), ("linkedin", 25), ("linkedin", 47), ("workday", 160)})


def test_a_longer_time_reads_further_back_and_a_list_cut_short_says_so(api, monkeypatch):
    """A read stops at a budget, `SCAN` for each day of the time asked (`scan_for`). At one day's budget
    whatever the time, the Jobs page showed the same newest jobs however long the shelf life was (owner,
    2026-11-07). Small here, so four jobs are more than a day's."""
    from libs import matching

    http, db, _, _ = api
    monkeypatch.setattr(matching, "SCAN", 2)
    ago = now() - timedelta(days=3)
    older = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                 title="Golang Developer", description="x", first_seen_at=ago, posted_at=ago,
                                 described_at=ago))
    for _ in range(2):
        insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                             title="Golang Developer", description="x", described_at=now()))

    day = http.get("/feed?since_hours=24").json()
    assert day["cut"], "four of the last day's jobs, read two at a time: the page says they are the newest"
    assert older.id not in _ids(day)
    week = http.get("/feed").json()
    assert older.id in _ids(week) and not week["cut"], "any time reads the whole week"


def test_a_read_that_reached_past_the_time_asked_is_not_called_cut(api, monkeypatch):
    """A read reaches a day before the time asked, for a board whose clock runs ahead of ours. A budget
    spent there has read the whole time asked, and the page must not say more match (2026-11-07)."""
    from libs import matching

    http, db, _, _ = api
    monkeypatch.setattr(matching, "SCAN", 3)
    ago = now() - timedelta(hours=30)
    for _ in range(2):
        insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                             title="Golang Developer", description="x", first_seen_at=ago, posted_at=ago,
                             described_at=ago))

    day = http.get("/feed?since_hours=24").json()
    assert len(day["jobs"]) == 2, "the two of the last day, not the two of the day before"
    assert not day["cut"]
    # And what the filters offer is counted in it: no "golang" for the jobs of the day before.
    assert {each["name"]: each["jobs"] for each in day["words"]} == {"backend engineer": 2}
    assert {each["name"]: each["jobs"] for each in day["sources"]} == {"test": 2}


def test_the_jobs_page_opens_on_the_last_day_and_as_they_left_it(api, caplog):
    """A new account's Jobs page opens on "Last 24 hours" (owner, 2026-11-07), and what they change is kept
    for the next time, on any device. Saved filters that no longer read are the defaults, said in the log."""
    http, db, user, _ = api
    opened = http.get("/dashboard").json()["job_filters"]
    assert (opened["since_hours"], opened["q"], opened["sort"], opened["word"]) == (24, "", "best", [])

    left = {**opened, "since_hours": None, "source": ["greenhouse", "lever"], "continent": ["EU"],
            "country": ["DE", "FR"], "city": ["DE/Berlin"], "word": ["golang"], "made": False}
    assert http.put("/feed/filters", json=left).json() == left
    assert http.get("/dashboard").json()["job_filters"] == left

    _change(db, user, job_filters={"since_hours": "a fortnight"})
    with caplog.at_level(logging.WARNING):
        assert http.get("/dashboard").json()["job_filters"] == opened
    assert "saved filters no longer read" in caplog.text


def _place(db, job, country: str, city: str | None = None) -> None:
    db[CorpusJob.COLLECTION].update_one({"_id": job.id}, {"$set": {"country": country, "city": city}})


def _searching(db, user, *countries: str) -> None:
    """Their search, in these countries: a job in a country they did not pick is not on their list at all."""
    from libs.db.models import UserConfig

    db[UserConfig.COLLECTION].update_one({"user_id": user.id}, {"$set": {"config": {
        **CONFIG, "sections": [{**CONFIG["sections"][0], "locations": list(countries)}]}}})


def test_each_continent_country_and_town_is_offered_with_its_count_and_where_it_is(api):
    """What "Where" offers (owner, 2026-11-24: "narrow down continent, then country and then city"): every
    continent, country and town their jobs are in, counted before any filter, each country with its continent
    and each town with its country, so the page can offer a country's towns once it is picked."""
    http, db, user, jobs = api
    _searching(db, user, "germany", "united_states")
    _place(db, jobs[0], "DE", "Berlin")
    _place(db, jobs[1], "US", "Berlin")

    body = http.get("/feed?country=DE").json()
    assert body["continents"] == [{"value": "EU", "label": "Europe", "jobs": 1, "within": None},
                                  {"value": "NA", "label": "North America", "jobs": 1, "within": None}]
    assert body["countries"] == [{"value": "DE", "label": "Germany", "jobs": 1, "within": "EU"},
                                 {"value": "US", "label": "United States", "jobs": 1, "within": "NA"}]
    assert body["cities"] == [{"value": "DE/Berlin", "label": "Berlin", "jobs": 1, "within": "DE"},
                              {"value": "US/Berlin", "label": "Berlin", "jobs": 1, "within": "US"}], (
        "two towns of one name are two places")
    assert _ids(http.get("/feed?city=US/Berlin").json()) == [jobs[1].id]


def test_as_many_places_as_are_picked_and_a_narrower_one_narrows_only_its_own(api):
    """Many of each (owner, 2026-11-24: "choose multiple of each"): a continent is every country on it, a
    country picked inside it narrows it to that country, and a town picked inside a country narrows that
    country alone; a place picked beside another adds to it."""
    http, db, user, jobs = api
    _searching(db, user, "germany", "france", "united_states")
    berlin, munich = jobs[0], jobs[1]
    _place(db, berlin, "DE", "Berlin")
    _place(db, munich, "DE", "Munich")
    paris, boston = (insert(db, CorpusJob(described_at=now(), url=f"https://example.com/{uuid.uuid4()}",
                                          source="test", title="Backend Engineer", description="x",
                                          country=country, city=city))
                     for country, city in (("FR", "Paris"), ("US", "Boston")))

    def found(query: str) -> set[int]:
        return set(_ids(http.get(f"/feed?{query}").json()))

    assert found("continent=EU") == {berlin.id, munich.id, paris.id}
    assert found("continent=EU&continent=NA") == {berlin.id, munich.id, paris.id, boston.id}
    assert found("continent=EU&country=FR") == {paris.id}, "a country picked on a continent narrows it"
    assert found("country=DE&country=FR") == {berlin.id, munich.id, paris.id}
    assert found("country=DE&city=DE/Berlin") == {berlin.id}, "a town picked in a country narrows it"
    assert found("country=FR&city=DE/Berlin") == {berlin.id, paris.id}, "and beside another adds to it"
    assert found("city=DE/Berlin&city=DE/Munich") == {berlin.id, munich.id}
    assert found("continent=EU&city=US/Boston") == {berlin.id, munich.id, paris.id, boston.id}
    assert http.get("/feed?city=berlin").status_code == 422, "a town is named with its country"


def test_jobs_from_the_job_sites_picked_and_from_no_other(api):
    """"Job sites", as many as somebody picks (owner, 2026-11-24: "select at least one, and more as well")."""
    http, db, _, jobs = api
    for job, source in zip(jobs, ("greenhouse", "lever", "linkedin"), strict=True):
        db[CorpusJob.COLLECTION].update_one({"_id": job.id}, {"$set": {"source": source}})

    assert set(_ids(http.get("/feed?source=greenhouse&source=lever").json())) == {jobs[0].id, jobs[1].id}
    assert _ids(http.get("/feed?source=lever").json()) == [jobs[1].id]


def test_a_card_names_the_country_its_place_was_read_as(api):
    http, db, _, jobs = api
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id}, {"$set": {"country": "DE"}})

    rows = {job["id"]: job for job in http.get("/feed").json()["jobs"]}
    assert (rows[jobs[0].id]["country"], rows[jobs[1].id]["country"]) == ("DE", None)


def test_newest_first_puts_the_latest_job_on_top_whatever_its_words(api):
    """Best first ranks a title match over a text match with as many words; newest first does not."""
    http, db, _, _jobs = api
    in_text = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                   title="Developer", description="a backend engineer role",
                                   first_seen_at=now() + timedelta(minutes=5), described_at=now()))

    assert _ids(http.get("/feed").json())[-1] == in_text.id, "a text match ranks below the titles"
    assert _ids(http.get("/feed?sort=newest").json())[0] == in_text.id


def test_just_arrived_first_puts_the_job_read_last_on_top_whatever_date_the_board_gives_it(api):
    """A job posted hours ago and read a minute ago is new to them; newest posted first puts it last. It
    arrives when its text is read, so one found earlier and read later is above one found and read
    between."""
    http, db, _, _jobs = api
    read_last = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                     title="Backend Engineer", posted_at=now() - timedelta(hours=3),
                                     first_seen_at=now() - timedelta(hours=3),
                                     described_at=now() + timedelta(minutes=5)))
    posted_last = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                       title="Backend Engineer", posted_at=now() + timedelta(minutes=1),
                                       first_seen_at=now() + timedelta(minutes=1),
                                       described_at=now() + timedelta(minutes=1)))

    assert _ids(http.get("/feed?sort=arrived").json())[:2] == [read_last.id, posted_last.id]
    assert _ids(http.get("/feed?sort=newest").json())[0] == posted_last.id
    assert _ids(http.get("/feed?sort=newest").json())[-1] == read_last.id


def test_a_list_carries_none_of_a_jobs_text_and_one_request_brings_the_texts_of_its_jobs(api):
    """A card showed the start of the description (owner, 2026-11-21: "remove the jobs descriptions, that
    would make the jobs appear cleaner too ... there should be no waiting to open a job"). The list has none
    of it, and one request answers with the whole text of every job a page lists, for the page to hold
    before any is opened. A job with no text, or no such job, is left out; a hundred is the most at once."""
    http, db, _, jobs = api
    long = "We build payment systems.  \n\n" + "Python and Postgres all day. " * 40
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id}, {"$set": {"description": long}})
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[1].id}, {"$set": {"description": "Short and sweet."}})

    listed = http.get("/feed")
    texts = http.get("/jobs/descriptions", params={"ids": [jobs[0].id, jobs[1].id, 999_999]})

    assert {jobs[0].id, jobs[1].id} <= {job["id"] for job in listed.json()["jobs"]}
    assert "payment systems" not in listed.text and "Short and sweet" not in listed.text
    assert texts.json()["texts"] == {str(jobs[0].id): long, str(jobs[1].id): "Short and sweet."}
    assert http.get(f"/jobs/{jobs[0].id}/description").json()["text"] == long, "one job by itself, the same"
    assert http.get("/jobs/descriptions", params={"ids": list(range(1, 102))}).status_code == 422
    assert http.get("/jobs/descriptions").status_code == 422


def test_a_description_stored_as_one_line_is_shown_in_paragraphs(api):
    """Every description read before 2026-11-02 is one line (The Muse's were 4,000 characters of it),
    and stays so until it ages out. A text with its own breaks comes back as it is (above)."""
    http, db, _, jobs = api
    heading = ("Requisition #: 20831 Functional Area: Software Development Employment Type: Full-Time "
               "Work Options: Remote Position Summary J. J. Keller is seeking a manager who will lead, "
               "develop and mentor a team of software engineers while ensuring the successful delivery of "
               "technology solutions that support the objectives of the business.")
    prose = [f"Sentence {n} says one more thing about the job, at about the length a sentence has."
             for n in range(6)]
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id},
                                        {"$set": {"description": " ".join([heading, *prose])}})

    text = http.get(f"/jobs/{jobs[0].id}/description").json()["text"]

    assert text.split("\n\n") == [heading, " ".join(prose[0:4]), " ".join(prose[4:])], \
        "a long heading run into its first sentence stands alone; \"J. J.\" ends no sentence"


def test_a_job_is_on_the_page_only_once_its_text_is_read(api):
    """Listed on its title alone, a job left the page when its text came in and said another language,
    a way of working not wanted, more applicants than the cap (owner, 2026-11-06). So it waits."""
    from libs.corpus.repository import CorpusRepository

    http, db, _, _ = api
    corpus = CorpusRepository(db)
    job_id = corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="linkedin",
                        title="Backend Engineer")
    assert job_id not in _ids(http.get("/feed").json())
    assert http.get(f"/jobs/{job_id}/description").status_code == 404

    corpus.set_description(job_id, "a teaser")
    assert job_id not in _ids(http.get("/feed").json()), "a teaser is not its text"

    corpus.set_description(job_id, "We build backend systems in Go and Postgres. " * 6)
    assert job_id in _ids(http.get("/feed").json())
    assert http.get("/jobs/descriptions", params={"ids": [job_id]}).json()["texts"][str(job_id)].startswith(
        "We build backend systems")


# ── one job, hidden, applied ──────────────────────────────────────────────────────────────────

def test_an_alerts_link_finds_its_job_while_it_is_still_theirs(api):
    """`/jobs?job=<id>` is where every alert lands: the job while it matches, nothing once it does not."""
    http, _, _, jobs = api
    assert _ids(http.get(f"/feed?job={jobs[1].id}").json()) == [jobs[1].id]
    assert http.get(f"/feed?job={jobs[2].id}").json()["total"] == 0, "the nurse never matched"

    http.put(f"/jobs/{jobs[1].id}/hidden")
    assert http.get(f"/feed?job={jobs[1].id}").json()["total"] == 0, "hidden is not in their list"


def test_an_alerts_link_opens_its_job_however_long_their_list_is(api, monkeypatch):
    """The job was looked for among the matches a page reads, the newest so many (`scan_for`): one older than
    that many of their matches opened as "not yours" from its own alert. And all of them were read and counted
    to show one job, 380 ms where the job alone takes 4 (2026-11-20). Here a page reads one match."""
    http, db, _user, jobs = api
    older, newer = jobs[0], jobs[1]
    db[CorpusJob.COLLECTION].update_one({"_id": older.id},
                                        {"$set": {"first_seen_at": now() - timedelta(hours=1)}})
    monkeypatch.setattr("services.api.dashboard_routes.scan_for", lambda _hours: 1)
    assert _ids(http.get("/feed").json()) == [newer.id], "the premise: the list as read stops at the newest"

    assert _ids(http.get(f"/feed?job={older.id}").json()) == [older.id]


def test_i_applied_counts_takes_the_job_off_the_jobs_page_and_can_be_taken_back(api):
    """Applied to is done with: it stays in Applied and never shows on the jobs page again, so
    nobody reads the same job twice, after a refresh or a lost connection (owner, 2026-11-02)."""
    http, db, user, jobs = api
    job = jobs[0]

    def listed():
        return {row["id"] for row in http.get("/feed").json()["jobs"]}

    assert http.put(f"/jobs/{job.id}/applied").json() == {
        "job_id": job.id, "applied": True, "hidden": False, "bookmarked": False}
    assert http.get("/dashboard").json()["applied"] == 1
    assert job.id not in listed()
    assert [row["id"] for row in http.get("/applied").json()["jobs"]] == [job.id]
    assert http.get(f"/feed?job={job.id}").json()["total"] == 0, "an alert's link says it is not theirs"

    assert http.delete(f"/jobs/{job.id}/applied").json()["applied"] is False
    assert http.get("/dashboard").json()["applied"] == 0
    assert job.id in listed()
    assert db[UserJobState.COLLECTION].count_documents({"user_id": user.id}) == 1, (
        "one row per person per job, however many times it is marked")


def test_a_skipped_job_leaves_the_jobs_page_until_it_is_shown_again(api):
    http, _db, _user, jobs = api
    job = jobs[0]

    def listed():
        return {row["id"] for row in http.get("/feed").json()["jobs"]}

    assert job.id in listed()
    assert http.put(f"/jobs/{job.id}/hidden").json()["hidden"] is True
    assert job.id not in listed()
    http.delete(f"/jobs/{job.id}/hidden")
    assert job.id in listed()


def test_only_a_real_job_and_the_marks_there_are_are_accepted(api):
    http, _db, _user, jobs = api
    assert http.put("/jobs/999999999/applied").status_code == 404
    assert http.put("/jobs/999999999/bookmarked").status_code == 404
    assert http.put(f"/jobs/{jobs[0].id}/sent").status_code == 422


def test_a_bookmarked_job_stays_on_the_jobs_page_and_is_listed_under_bookmarks_latest_first(api):
    """A little mark on a job (owner, 2026-11-15): it takes nothing off the Jobs page, the job says it is
    bookmarked there, and Bookmarks lists it as the Jobs page shows a job, the latest bookmarked first."""
    http, _db, _user, jobs = api
    first, senior, nurse = jobs

    def on_the_page() -> dict[int, bool]:
        return {row["id"]: row["bookmarked"] for row in http.get("/feed").json()["jobs"]}

    def listed() -> list[dict]:
        return http.get("/jobs/bookmarked").json()["jobs"]

    assert on_the_page() == {first.id: False, senior.id: False}
    assert _page(http.get("/jobs/bookmarked").json()) == ([], 0)

    assert http.put(f"/jobs/{first.id}/bookmarked").json()["bookmarked"] is True
    # One that is not on their list can be bookmarked too: it was shown to them by its link, or was theirs.
    assert http.put(f"/jobs/{nurse.id}/bookmarked").json()["bookmarked"] is True

    assert on_the_page() == {first.id: True, senior.id: False}
    cards = listed()
    assert [row["id"] for row in cards] == [nurse.id, first.id]
    assert all(row["bookmarked"] for row in cards)
    assert {"title", "company", "salary", "apply_on", "documents"} <= set(cards[0]), "a card like any other"
    assert [row["words"] for row in cards] == [[], ["backend engineer"]], "and it says why, as it did there"

    assert http.delete(f"/jobs/{nurse.id}/bookmarked").json()["bookmarked"] is False
    assert [row["id"] for row in listed()] == [first.id]
    assert on_the_page() == {first.id: True, senior.id: False}


def test_a_bookmark_is_one_persons_and_leaves_bookmarks_when_they_apply_skip_or_the_job_closes(api):
    http, db, _user, jobs = api
    first, senior, nurse = jobs
    other = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                            active=True))
    record_state(db, other.id, senior.id, bookmarked_at=now())
    assert http.get("/jobs/bookmarked").json()["jobs"] == [], "somebody else's bookmark is not theirs"

    for job in jobs:
        http.put(f"/jobs/{job.id}/bookmarked")
    http.put(f"/jobs/{first.id}/applied")
    http.put(f"/jobs/{senior.id}/hidden")
    assert [row["id"] for row in http.get("/jobs/bookmarked").json()["jobs"]] == [nurse.id]
    assert [row["id"] for row in http.get("/applied").json()["jobs"]] == [first.id]

    db[CorpusJob.COLLECTION].update_one({"_id": nurse.id}, {"$set": {"closed_at": now()}})
    assert _page(http.get("/jobs/bookmarked").json()) == ([], 0)


def test_more_bookmarks_than_a_page_are_counted_whole_and_come_a_page_at_a_time(api):
    """The first hundred marks were read and the closed ones dropped after, and the page's heading counted the
    cards it got: somebody with more was told a smaller number and had no way to the rest. Now a page of the
    open ones, as long as a page of Jobs, the count of all of them, and the next page from where this ends."""
    from services.api.dashboard_routes import FEED_PAGE

    http, db, user, _jobs = api
    marked = []
    for n in range(FEED_PAGE + 4):
        job = insert(db, CorpusJob(described_at=now(), url=f"https://example.com/{uuid.uuid4()}",
                                   source="test", title=f"Bookkeeper {n}", description="we keep books"))
        record_state(db, user.id, job.id, bookmarked_at=now() - timedelta(minutes=n))
        marked.append(job.id)
    # Two of the latest have closed since: they are neither shown nor counted, and do not shorten the page.
    closed = marked[1:3]
    db[CorpusJob.COLLECTION].update_many({"_id": {"$in": closed}}, {"$set": {"closed_at": now()}})
    still = [job_id for job_id in marked if job_id not in closed]

    first = http.get("/jobs/bookmarked").json()
    assert first["total"] == len(still) == FEED_PAGE + 2
    assert [row["id"] for row in first["jobs"]] == still[:FEED_PAGE], "a whole page, the latest first"
    rest = http.get(f"/jobs/bookmarked?offset={FEED_PAGE}").json()
    assert [row["id"] for row in rest["jobs"]] == still[FEED_PAGE:], "and the rest, each once"
    assert rest["total"] == first["total"]
    assert _page(http.get(f"/jobs/bookmarked?offset={len(still)}").json()) == ([], len(still))


def test_bookmarks_are_searched_and_filtered_as_the_jobs_page_is_and_offer_what_all_of_them_have(api):
    """Owner, 2026-11-24: Bookmarks "should also have a basic search bar and filtering function too": the
    same search and filters as Jobs, counted over the jobs left, and what the filters offer counted over all
    of the bookmarks, so another can be picked."""
    http, db, _user, jobs = api
    first, senior, nurse = jobs
    _place(db, first, "DE", "Berlin")
    _place(db, nurse, "FR", "Paris")
    db[CorpusJob.COLLECTION].update_one({"_id": nurse.id}, {"$set": {"source": "lever", "company": "Clinic",
                                                                     "location": "Paris, France"}})
    for job in jobs:
        http.put(f"/jobs/{job.id}/bookmarked")

    assert _page(http.get("/jobs/bookmarked?q=nurse").json()) == ([nurse.id], 1)
    assert _page(http.get("/jobs/bookmarked?q=clinic").json()) == ([nurse.id], 1), "its company too"
    assert _page(http.get("/jobs/bookmarked?q=paris").json()) == ([nurse.id], 1), "and its place"
    assert _page(http.get("/jobs/bookmarked?country=DE").json()) == ([first.id], 1)
    assert _page(http.get("/jobs/bookmarked?city=FR/Paris&source=lever").json()) == ([nurse.id], 1)
    assert _page(http.get("/jobs/bookmarked?source=test").json()) == ([senior.id, first.id], 2)

    offers = http.get("/jobs/bookmarked?country=DE").json()["offers"]
    assert [(each["value"], each["jobs"]) for each in offers["countries"]] == [("DE", 1), ("FR", 1)]
    assert [(each["value"], each["jobs"]) for each in offers["cities"]] == [("DE/Berlin", 1), ("FR/Paris", 1)]
    assert [(each["name"], each["jobs"]) for each in offers["sources"]] == [("test", 2), ("lever", 1)]
    assert http.get("/jobs/added").json()["offers"] is None, "a list with no filters offers none"


def test_applied_is_filtered_by_search_place_stage_and_day_and_still_tells_the_whole_record(api):
    """Owner, 2026-11-24: Applied "allows people to filter all of their jobs, just like on the other pages",
    and a calendar of the days they applied. The jobs are the ones the filters leave; how many there are, the
    day each was applied to and how many stand at each stage are the whole record, whatever is picked."""
    http, db, user, jobs = api
    first, senior, nurse = jobs
    _place(db, nurse, "FR", "Paris")
    for job in jobs:
        http.put(f"/jobs/{job.id}/applied")
    two_days_ago = now() - timedelta(days=2)
    db[UserJobState.COLLECTION].update_one({"user_id": user.id, "job_id": first.id},
                                           {"$set": {"applied_at": two_days_ago}})
    http.put(f"/jobs/{senior.id}/progress", json={"stage": "interview"})

    def shown(query: str = "") -> list[int]:
        return _ids(http.get(f"/applied?{query}").json())

    assert shown() == [nurse.id, senior.id, first.id]
    assert shown("q=nurse") == [nurse.id]
    assert shown("country=FR") == shown("continent=EU&city=FR/Paris") == [nurse.id]
    assert shown("stage=interview") == [senior.id]
    assert shown("stage=applied") == [nurse.id, first.id], "nothing heard yet"
    day = two_days_ago.replace(hour=0, minute=0, second=0, microsecond=0)
    assert shown(f"applied_from={day.isoformat()}&applied_to={(day + timedelta(days=1)).isoformat()}"
                 .replace("+", "%2B")) == [first.id]

    body = http.get("/applied?stage=offer").json()
    assert body["jobs"] == []
    assert (body["total"], len(body["dates"]), body["stages"]) == (3, 3, {"applied": 2, "interview": 1})
    assert body["dates"] == sorted(body["dates"], reverse=True), "the latest first"
    assert [(each["value"], each["jobs"]) for each in body["offers"]["countries"]] == [("FR", 1)]


def test_filters_saved_before_places_were_lists_open_as_they_were_left(api):
    """Saved before 2026-11-24, a job site, a country and a city were one each, the city by its slug: they
    open as a list of it, the city named as postings now name their town, rather than all filters lost."""
    http, db, user, _ = api
    _change(db, user, job_filters={"source": "greenhouse", "country": "DE", "city": "munich", "q": "go"})

    opened = http.get("/dashboard").json()["job_filters"]
    assert (opened["source"], opened["country"], opened["city"], opened["q"]) == (
        ["greenhouse"], ["DE"], ["DE/Munich"], "go")
    _change(db, user, job_filters={"source": "", "country": "", "city": ""})
    opened = http.get("/dashboard").json()["job_filters"]
    assert (opened["source"], opened["country"], opened["city"]) == ([], [], [])


def test_every_stored_posting_is_given_its_town_once_by_the_reader_of_every_town(api):
    """Before 2026-11-24 a posting's town was one of 43 cities or none, so half the jobs had none. The deploy
    reads every stored posting's place again, once ever (`python -m libs.db`)."""
    from libs.corpus.repository import read_places_once

    _http, db, _user, jobs = api
    db[CorpusJob.COLLECTION].update_many({}, {"$set": {"country": None, "city": None}})
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id}, {"$set": {"location": "Debrecen, Hungary"}})
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[1].id}, {"$set": {"location": "Wernau (Neckar), DE"}})

    assert read_places_once(db) == 2
    placed = {row["_id"]: (row["country"], row["city"]) for row in db[CorpusJob.COLLECTION].find(
        {"_id": {"$in": [job.id for job in jobs]}})}
    assert placed == {jobs[0].id: ("HU", "Debrecen"), jobs[1].id: ("DE", "Wernau"), jobs[2].id: (None, None)}
    db[CorpusJob.COLLECTION].update_many({}, {"$set": {"city": None}})
    assert read_places_once(db) == 0, "once ever"


# ── the landing page ──────────────────────────────────────────────────────────────────────────

def test_the_ats_figure_is_said_only_for_the_cv_wording_it_was_measured_on(api, monkeypatch):
    """A figure of our own on the landing page (owner, 2026-11-15: on the site if "above 95.3%"), with what
    was tested and when. It is about one wording of `write_cv.md`: change the prompt and the page says no
    figure until the bench has measured the new one and `ATS_MEASURED` says so. **If this fails after a prompt
    change, measure again; do not edit the version alone.**"""
    from libs import prompts
    from services.api.dashboard_routes import ATS_MEASURED

    http, *_ = api
    assert ATS_MEASURED.prompt == prompts.version("write_cv.md"), (
        "write_cv.md changed since the ATS figure was measured: the landing page now says none")
    assert ATS_MEASURED.passed / ATS_MEASURED.of > 0.953
    assert http.get("/stats").json()["ats"] == ATS_MEASURED.model_dump()

    monkeypatch.setattr(prompts, "version", lambda name: "another wording")
    assert http.get("/stats").json()["ats"] is None


