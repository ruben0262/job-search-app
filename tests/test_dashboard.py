"""The front page and the jobs page, both on `libs.matching`: what a person sees, and why."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

# A stored config as accounts actually have them, retired field and all.
CONFIG = {"sections": [{"platform": "all", "keywords": ["backend engineer"]}],
          "max_notifications_per_day": 20}


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import CorpusJob, User, UserConfig
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                active=True, full_name="Alice Example", telegram_status="connected",
                telegram_chat_id="1", onboarding_completed_at=datetime.now(UTC))
    session.add(user)
    session.flush()
    session.add(UserConfig(user_id=user.id, config=CONFIG, schema_version=2))

    jobs = [CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                      title=title, description="we build things", is_remote=True)
            for title in ("Backend Engineer", "Senior Backend Engineer", "Registered Nurse")]
    session.add_all(jobs)
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, session, user, jobs
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


def _ids(body: dict) -> list[int]:
    return [job["id"] for job in body["jobs"]]


# ── the front page ────────────────────────────────────────────────────────────────────────────

def test_an_unfinished_signup_gets_the_onboarding_state_and_nothing_else(api):
    """Empty panels read as a broken product rather than an unfinished signup."""
    http, session, user, _ = api
    user.onboarding_completed_at = None
    session.flush()

    body = http.get("/dashboard").json()
    assert body["stage"] == "onboarding"
    assert body["onboarding"], "the onboarding state is passed through for the signup screens"
    assert "matching" not in body


def test_the_front_page_counts_applications_and_never_runs_the_match(api, monkeypatch):
    """The jobs page counts its own list, so the dashboard every page waits for must not run a second
    match: that was 150 ms of a 13 ms answer (median, 2,000 rows)."""
    from sqlalchemy import func

    from libs.db.models import UserJobState

    http, session, user, jobs = api
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, applied_at=func.now()))
    session.flush()

    def no_match(*_args, **_kwargs):
        raise AssertionError("the dashboard ran the match")

    monkeypatch.setattr("services.api.dashboard_routes.matching_jobs", no_match)
    front = http.get("/dashboard").json()
    assert front["stage"] == "ready"
    assert front["applied"] == 1


def test_a_stopped_telegram_is_a_reconnect_offer_not_an_error(api):
    """Their old per-user bot, or they blocked the platform's: the page offers to reconnect."""
    http, session, user, _ = api
    user.telegram_status = "broken"
    session.flush()

    banners = http.get("/dashboard").json()["banners"]
    assert [banner["key"] for banner in banners] == ["telegram_broken"]
    assert banners[0]["level"] != "error", "nothing is broken that the person cannot mend in one tap"


def test_with_no_alerts_at_all_the_page_offers_them_quietly(api):
    """Alerts are optional, and a browser is a channel: "nothing is being delivered" was wrong twice."""
    from libs.db.models import PushSubscription

    http, session, user, _ = api
    user.telegram_status, user.telegram_chat_id = "unconfigured", None
    session.flush()
    banners = http.get("/dashboard").json()["banners"]
    assert [(banner["key"], banner["level"]) for banner in banners] == [("no_alerts", "info")]

    session.add(PushSubscription(user_id=user.id, endpoint=f"https://push.example/{uuid.uuid4()}",
                                 p256dh="key", auth="auth"))
    session.flush()
    assert http.get("/dashboard").json()["banners"] == [], "notifications on in a browser are enough"


def test_a_paused_account_is_told_it_is_paused(api):
    """It used to be said only to accounts with a trained model, which after Phase 8 is nobody."""
    http, session, user, _ = api
    assert http.get("/dashboard").json()["banners"] == [], "alerts on and working: nothing to say"
    user.active = False
    session.flush()

    keys = {banner["key"] for banner in http.get("/dashboard").json()["banners"]}
    assert keys == {"paused"}


def test_the_front_page_says_whether_ai_can_write_for_them_and_on_whose_key(api, monkeypatch):
    """The AI buttons and "AI requests left today" are `libs.llm.resolve`'s own answer, not a guess."""
    from libs.db.models import AiRequest
    from libs.secrets import KeyRing, SecretStore, generate_key

    http, session, user, _ = api
    monkeypatch.delenv("PLATFORM_AI_PROVIDER", raising=False)
    monkeypatch.delenv("PLATFORM_AI_KEY", raising=False)
    ai = http.get("/dashboard").json()["ai"]
    assert (ai["payer"], ai["left"], ai["per_day"]) == (None, 20, 20), "no key anywhere: no AI"
    assert ai["profile"] is False, "a new account has no writing profile, so only instant documents"

    monkeypatch.setenv("PLATFORM_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PLATFORM_AI_KEY", "AIza" + "k" * 30)
    session.add_all([AiRequest(user_id=user.id) for _ in range(3)])
    session.flush()
    ai = http.get("/dashboard").json()["ai"]
    assert (ai["payer"], ai["left"]) == ("platform", 17)

    kek = f"k1:{generate_key()}"
    SecretStore(session, KeyRing.from_env({"SECRETS_KEK": kek})).put(
        user.id, "openai_api", "sk-proj-" + "o" * 30, validated=True)
    session.flush()
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
        assert job["score"] == 75, "a green word in the title"


def test_a_red_word_takes_a_job_off_the_page(api):
    from libs.db.models import UserConfig

    http, session, user, jobs = api
    session.get(UserConfig, user.id).config = {"sections": [{
        "platform": "all", "keywords": ["backend engineer"], "exclude_keywords": ["senior"]}]}
    session.flush()

    assert _ids(http.get("/feed").json()) == [jobs[0].id]


def test_a_dismissed_job_stays_off_the_page(api):
    """Dismissing has to mean something, or the button is a lie."""
    from sqlalchemy import func

    from libs.db.models import UserJobState

    http, session, user, jobs = api
    session.add(UserJobState(user_id=user.id, job_id=jobs[0].id, dismissed_at=func.now()))
    session.flush()

    body = http.get("/feed").json()
    assert _ids(body) == [jobs[1].id]
    assert body["total"] == 1


def test_the_feed_does_not_carry_the_full_description(api):
    """Up to 2,500 characters a job, and the list does not show it."""
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
    from libs.db.models import CorpusJob

    http, session, _, jobs = api
    unknown = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                        title="Backend Engineer II", description="x", is_remote=None)
    session.add(unknown)
    session.flush()

    assert unknown.id in _ids(http.get("/feed").json())
    assert set(_ids(http.get("/feed?remote=true").json())) == {jobs[0].id, jobs[1].id}


def test_a_salary_in_the_description_reaches_the_row_without_the_description(api):
    """The one thing the description holds that the table wants, extracted server-side for free."""
    http, session, _, jobs = api
    jobs[0].description = "Backend role paying €60,000 - €80,000 per year. We build things."
    session.flush()

    row = next(job for job in http.get("/feed").json()["jobs"] if job["id"] == jobs[0].id)
    assert row["salary"]["min"] == 60000
    assert row["salary"]["max"] == 80000
    assert row["salary"]["currency"] == "EUR"
    assert "description" not in row


def test_equal_scores_are_newest_first(api):
    """Freshness breaks a tie: the order `libs.matching` gives, not a second one made up here."""
    from libs.db.models import CorpusJob

    http, session, _, _ = api
    now = datetime.now(UTC)
    rows = {age: CorpusJob(url=f"https://example.test/tie-{age}", source="test",
                           title="Backend Engineer", description="x", is_remote=True,
                           first_seen_at=now - timedelta(hours=age))
            # The older one first, so its id is the lower: insertion order cannot pass for freshness.
            for age in (5, 1)}
    session.add_all(rows.values())
    session.flush()

    order = [i for i in _ids(http.get("/feed").json()) if i in {r.id for r in rows.values()}]
    assert order == [rows[1].id, rows[5].id]


# ── one job, hidden, applied ──────────────────────────────────────────────────────────────────

def test_an_alerts_link_finds_its_job_while_it_is_still_theirs(api):
    """`#/feed?job=<id>` is where every alert lands: the job while it matches, nothing once it does not."""
    http, _, _, jobs = api
    assert _ids(http.get(f"/feed?job={jobs[1].id}").json()) == [jobs[1].id]
    assert http.get(f"/feed?job={jobs[2].id}").json()["total"] == 0, "the nurse never matched"

    http.put(f"/jobs/{jobs[1].id}/hidden")
    assert http.get(f"/feed?job={jobs[1].id}").json()["total"] == 0, "hidden is not in their list"


def test_i_applied_counts_on_the_front_page_and_can_be_taken_back(api):
    http, _session, _user, jobs = api
    job = jobs[0]

    assert http.put(f"/jobs/{job.id}/applied").json() == {
        "job_id": job.id, "applied": True, "hidden": False}
    assert http.get("/dashboard").json()["applied"] == 1
    assert next(row for row in http.get("/feed").json()["jobs"] if row["id"] == job.id)["applied_at"]

    assert http.delete(f"/jobs/{job.id}/applied").json()["applied"] is False
    assert http.get("/dashboard").json()["applied"] == 0


def test_a_hidden_job_leaves_the_jobs_page_until_it_is_shown_again(api):
    http, _session, _user, jobs = api
    job = jobs[0]

    def listed():
        return {row["id"] for row in http.get("/feed").json()["jobs"]}

    assert job.id in listed()
    assert http.put(f"/jobs/{job.id}/hidden").json()["hidden"] is True
    assert job.id not in listed()
    http.delete(f"/jobs/{job.id}/hidden")
    assert job.id in listed()


def test_only_a_real_job_and_the_two_marks_are_accepted(api):
    http, _session, _user, jobs = api
    assert http.put("/jobs/999999999/applied").status_code == 404
    assert http.put(f"/jobs/{jobs[0].id}/sent").status_code == 422
