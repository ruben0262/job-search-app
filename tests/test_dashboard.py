"""The front page and the jobs page, both on `libs.matching`: what a person sees, and why."""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest

from libs.db import insert, now
from libs.db.models import CorpusJob, User, UserJobState

pytest.importorskip("fastapi")

# A stored config as accounts actually have them, retired field and all.
CONFIG = {"sections": [{"platform": "all", "keywords": ["backend engineer"]}],
          "max_notifications_per_day": 20}


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db.models import UserConfig
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                           active=True, full_name="Alice Example", telegram_status="connected",
                           telegram_chat_id="1", onboarding_completed_at=now()))
    insert(db, UserConfig(user_id=user.id, config=CONFIG, schema_version=2))

    jobs = [insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                 title=title, description="we build things", is_remote=True))
            for title in ("Backend Engineer", "Senior Backend Engineer", "Registered Nurse")]

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user, jobs


def _ids(body: dict) -> list[int]:
    return [job["id"] for job in body["jobs"]]


def _change(db, user, **fields) -> None:
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": fields})


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

    monkeypatch.setattr("services.api.dashboard_routes.matching_jobs", no_match)
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


def test_with_no_alerts_at_all_the_page_offers_them_quietly(api):
    """Alerts are optional, and a browser is a channel: "nothing is being delivered" was wrong twice."""
    from libs.db.models import PushSubscription

    http, db, user, _ = api
    _change(db, user, telegram_status="unconfigured", telegram_chat_id=None)
    banners = http.get("/dashboard").json()["banners"]
    assert [(banner["key"], banner["level"]) for banner in banners] == [("no_alerts", "info")]

    insert(db, PushSubscription(user_id=user.id, endpoint=f"https://push.example/{uuid.uuid4()}",
                                p256dh="key", auth="auth"))
    assert http.get("/dashboard").json()["banners"] == [], "notifications on in a browser are enough"


def test_a_paused_account_is_told_it_is_paused(api):
    """It used to be said only to accounts with a trained model, which after Phase 8 is nobody."""
    http, db, user, _ = api
    assert http.get("/dashboard").json()["banners"] == [], "alerts on and working: nothing to say"
    _change(db, user, active=False)

    keys = {banner["key"] for banner in http.get("/dashboard").json()["banners"]}
    assert keys == {"paused"}


def test_the_front_page_says_whether_ai_can_write_for_them_and_on_whose_key(api, monkeypatch):
    """The AI buttons and "AI requests left today" are `libs.llm.resolve`'s own answer, not a guess."""
    from libs.db.models import AiRequest
    from libs.secrets import KeyRing, SecretStore, generate_key

    http, db, user, _ = api
    monkeypatch.delenv("PLATFORM_AI_PROVIDER", raising=False)
    monkeypatch.delenv("PLATFORM_AI_KEY", raising=False)
    ai = http.get("/dashboard").json()["ai"]
    assert (ai["payer"], ai["left"], ai["per_day"]) == (None, 20, 20), "no key anywhere: no AI"
    assert ai["profile"] is False, "a new account has no writing profile, so only instant documents"

    monkeypatch.setenv("PLATFORM_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PLATFORM_AI_KEY", "AIza" + "k" * 30)
    for _ in range(3):
        insert(db, AiRequest(user_id=user.id))
    ai = http.get("/dashboard").json()["ai"]
    assert (ai["payer"], ai["left"]) == ("platform", 17)

    kek = f"k1:{generate_key()}"
    SecretStore(db, KeyRing.from_env({"SECRETS_KEK": kek})).put(
        user.id, "openai_api", "sk-proj-" + "o" * 30, validated=True)
    assert http.get("/dashboard").json()["ai"]["payer"] == "own", "their own key first, unlimited"


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
                                   title="Backend Engineer II", description="x", is_remote=None))

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
                                      first_seen_at=moment - timedelta(hours=age)))
            # The older one first, so its id is the lower: insertion order cannot pass for freshness.
            for age in (5, 1)}

    order = [i for i in _ids(http.get("/feed").json()) if i in {r.id for r in rows.values()}]
    assert order == [rows[1].id, rows[5].id]


def test_posted_lately_keeps_only_jobs_from_the_last_hours_asked(api):
    """By the posting date where the board gives one, else when it was found. Within the freshness
    window every board already has: this narrows what is shown, it never brings back older jobs."""
    http, db, _, jobs = api
    found_earlier = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                         title="Backend Engineer", description="x",
                                         first_seen_at=now() - timedelta(hours=4)))
    posted_earlier = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                          title="Backend Engineer", description="x", first_seen_at=now(),
                                          posted_at=now() - timedelta(hours=4)))
    earlier = {found_earlier.id, posted_earlier.id}

    assert earlier <= set(_ids(http.get("/feed").json())), "the premise: both are theirs"
    shown = set(_ids(http.get("/feed?since_hours=2").json()))
    assert {jobs[0].id, jobs[1].id} <= shown
    assert not shown & earlier


def test_one_board_at_a_time_and_every_board_is_offered_with_its_count(api):
    http, db, _, _jobs = api
    other = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="greenhouse",
                                 title="Backend Engineer", description="x"))

    body = http.get("/feed?source=greenhouse").json()

    assert _ids(body) == [other.id]
    assert {each["name"]: each["jobs"] for each in body["sources"]} == {"test": 2, "greenhouse": 1}, (
        "every board, counted before the filter, so another can be picked")


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


def test_the_jobs_found_after_a_list_was_read_are_counted_as_new(api):
    """What the "new jobs" button counts, without reloading the list under the person reading it."""
    http, db, _, _ = api
    as_of = http.get("/feed").json()["as_of"]
    assert http.get("/feed", params={"found_after": as_of}).json()["total"] == 0

    later = now() + timedelta(seconds=1)
    for title in ("Backend Engineer", "Registered Nurse"):
        insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title=title,
                             description="x", first_seen_at=later))

    assert http.get("/feed", params={"found_after": as_of, "limit": 1}).json()["total"] == 1


def test_one_country_or_one_city_and_each_offered_with_its_count(api):
    http, db, _, jobs = api
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id}, {"$set": {"country": "DE", "city": "berlin"}})
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[1].id}, {"$set": {"country": "DE", "city": "munich"}})

    body = http.get("/feed").json()
    assert body["countries"] == [{"value": "DE", "label": "Germany", "jobs": 2}]
    assert {(each["value"], each["label"]) for each in body["cities"]} == {("berlin", "Berlin"),
                                                                          ("munich", "Munich")}
    assert set(_ids(http.get("/feed?country=DE").json())) == {jobs[0].id, jobs[1].id}
    assert _ids(http.get("/feed?city=munich").json()) == [jobs[1].id]


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
                                   first_seen_at=now() + timedelta(minutes=5)))

    assert _ids(http.get("/feed").json())[-1] == in_text.id, "a text match ranks below the titles"
    assert _ids(http.get("/feed?sort=newest").json())[0] == in_text.id


def test_a_card_gets_the_start_of_the_description_and_the_rest_on_request(api):
    """A summary cut at a word, said to have more; the whole text from its own endpoint, so the list
    stays light."""
    http, db, _, jobs = api
    long = "We build payment systems.  \n\n" + "Python and Postgres all day. " * 40
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id}, {"$set": {"description": long}})
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[1].id}, {"$set": {"description": "Short and sweet."}})

    rows = {job["id"]: job for job in http.get("/feed").json()["jobs"]}

    summary = rows[jobs[0].id]["summary"]
    assert summary.startswith("We build payment systems. Python and Postgres") and summary.endswith("…")
    assert len(summary) <= 281 and " Postgres all day… " not in summary, "cut at a word"
    assert rows[jobs[0].id]["more"] is True
    assert (rows[jobs[1].id]["summary"], rows[jobs[1].id]["more"]) == ("Short and sweet.", False)
    assert http.get(f"/jobs/{jobs[0].id}/description").json()["text"] == long


def test_a_job_not_described_yet_has_no_summary_and_no_more(api):
    http, db, _, jobs = api
    db[CorpusJob.COLLECTION].update_one({"_id": jobs[0].id}, {"$set": {"description": None}})

    row = next(job for job in http.get("/feed").json()["jobs"] if job["id"] == jobs[0].id)

    assert (row["summary"], row["more"]) == (None, False)
    assert http.get(f"/jobs/{jobs[0].id}/description").status_code == 404


# ── one job, hidden, applied ──────────────────────────────────────────────────────────────────

def test_an_alerts_link_finds_its_job_while_it_is_still_theirs(api):
    """`#/feed?job=<id>` is where every alert lands: the job while it matches, nothing once it does not."""
    http, _, _, jobs = api
    assert _ids(http.get(f"/feed?job={jobs[1].id}").json()) == [jobs[1].id]
    assert http.get(f"/feed?job={jobs[2].id}").json()["total"] == 0, "the nurse never matched"

    http.put(f"/jobs/{jobs[1].id}/hidden")
    assert http.get(f"/feed?job={jobs[1].id}").json()["total"] == 0, "hidden is not in their list"


def test_i_applied_counts_takes_the_job_off_the_jobs_page_and_can_be_taken_back(api):
    """Applied to is done with: it stays in Applied and never shows on the jobs page again, so
    nobody reads the same job twice, after a refresh or a lost connection (owner, 2026-11-02)."""
    http, db, user, jobs = api
    job = jobs[0]

    def listed():
        return {row["id"] for row in http.get("/feed").json()["jobs"]}

    assert http.put(f"/jobs/{job.id}/applied").json() == {
        "job_id": job.id, "applied": True, "hidden": False}
    assert http.get("/dashboard").json()["applied"] == 1
    assert job.id not in listed()
    assert [row["id"] for row in http.get("/applied").json()] == [job.id]
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


def test_only_a_real_job_and_the_two_marks_are_accepted(api):
    http, _db, _user, jobs = api
    assert http.put("/jobs/999999999/applied").status_code == 404
    assert http.put(f"/jobs/{jobs[0].id}/sent").status_code == 422
