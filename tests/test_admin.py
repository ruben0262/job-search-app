"""The admin endpoints, and who is refused them."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.db import get, insert, now
from libs.db.models import User

pytest.importorskip("fastapi")

ADMIN = "operator@example.com"


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from services.api.app import app

    operator = insert(db, User(email=ADMIN, password_hash="x", subscription_status="active", status="active"))
    ordinary = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                               subscription_status="active", status="active"))

    def sign_in(http, user):
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")

    with TestClient(app, base_url="https://testserver") as http:
        yield http, db, operator, ordinary, sign_in


def _change(db, user, **fields) -> None:
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": fields})


def test_an_operator_sees_the_platform_and_its_alerts(api):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    body = http.get("/admin/health").json()
    assert {"lanes", "corpus_rows", "alerts", "summary"} <= set(body)
    assert isinstance(body["alerts"], list)


def test_an_account_page_says_what_they_do_with_the_site_and_nothing_of_the_setup_files(api):
    """For working out what to improve (owner, 2026-11-04, -10): the jobs waiting for them and how many came
    in since they were last here, how far their applications got, their search with the jobs each word and
    country finds, where their jobs come from, their files, their AI account, their alerts, and when they
    last did each. The setup agents' files went with the agents, and an incomplete list of them read as an
    error."""
    from datetime import timedelta

    from libs.db.models import CorpusJob, UserConfig, UserDocument, UserJobState
    from libs.tenant.repository import TenantRepository

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    TenantRepository(db).save_config(ordinary.id, {"languages": ["en", "de"], "sections": [
        {"platform": "all", "keywords": ["nurse", "midwife", "carer"], "exclude_keywords": ["night"],
         "locations": ["germany"], "workplaces": ["onsite"]}]})
    _change(db, ordinary, last_active_at=now() - timedelta(hours=2), email_alerts=True)
    jobs = [insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title=title,
                                 location="Berlin, Germany", country="DE", description="A ward job. " * 30,
                                 first_seen_at=now() - timedelta(hours=hours),
                                 described_at=now() - timedelta(hours=hours)))
            for title, hours in (("Nurse", 5), ("Midwife", 1), ("Carer", 1), ("Nurse Lead", 1))]
    insert(db, UserJobState(user_id=ordinary.id, job_id=jobs[0].id, applied_at=now(), notified_at=now(),
                            progress=[{"stage": "interview", "at": now()}]))
    insert(db, UserJobState(user_id=ordinary.id, job_id=jobs[3].id, dismissed_at=now()))
    insert(db, UserDocument(user_id=ordinary.id, kind="cv", filename="cv.pdf", text="x", text_sha256="1",
                            chars=1))

    body = http.get(f"/admin/users/{ordinary.id}").json()

    assert body["user"]["email"] == ordinary.email and "setup" not in body
    usage = body["usage"]
    assert (usage["jobs"], usage["new_jobs"]) == (2, 2), "skipped and applied are gone from their list"
    reached = usage["applications"]
    assert (reached["applied"], reached["interview"], reached["offer"]) == (1, 1, 0)
    assert (usage["skipped"], usage["delivered"]) == (1, 1)
    # Each word with the jobs on their list that have it: "nurse" finds only the two that left it.
    assert usage["green_words"] == [{"name": "carer", "jobs": 1}, {"name": "midwife", "jobs": 1},
                                    {"name": "nurse", "jobs": 0}]
    assert usage["red_words"] == ["night"]
    assert (usage["countries"], usage["workplaces"], usage["languages"]) == (
        [{"name": "Germany", "jobs": 2}], ["On-site"], ["English", "German"])
    assert usage["sources"] == [{"name": "Test", "jobs": 2}]
    assert (usage["cvs"], usage["cvs_in_use"], usage["alerts"]) == (1, 1, ["email"])
    assert (usage["cvs_made"], usage["covers_made"]) == (0, 0)
    assert (usage["ai_accounts"], usage["ai_in_use"]) == ([], None)
    assert usage["cv_look"] and usage["cover_look"]
    assert usage["last_applied_at"] and usage["last_alert_at"] and usage["last_made_at"] is None
    assert db[UserConfig.COLLECTION].count_documents({"user_id": ordinary.id}) == 1


def test_opening_the_site_is_when_they_were_last_online(api):
    """It moved only when somebody signed in, and a session lasts for weeks: an account used every day
    read as last active a month ago."""
    from datetime import timedelta

    http, db, _operator, ordinary, sign_in = api
    _change(db, ordinary, last_active_at=now() - timedelta(days=30))
    sign_in(http, ordinary)

    http.get("/dashboard")

    assert now() - get(db, User, ordinary.id).last_active_at < timedelta(minutes=1)


def test_an_ordinary_user_gets_a_404_not_a_403(api):
    """A 403 confirms the endpoint exists and that somebody is worth phishing for access to it."""
    http, _, _, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.get("/admin/health").status_code == 404
    assert http.get("/admin/users").status_code == 404


def test_admin_is_an_environment_variable_not_a_column(api, monkeypatch):
    """Granting admin is a deploy, which is the friction it should have."""
    http, _db, operator, _, sign_in = api
    sign_in(http, operator)
    assert http.get("/admin/health").status_code == 200

    monkeypatch.setenv("ADMIN_EMAILS", "somebody-else@example.com")
    assert http.get("/admin/health").status_code == 404


def test_no_admin_configured_means_nobody_is_an_admin(api, monkeypatch):
    """An empty variable must not mean "everybody"."""
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    monkeypatch.setenv("ADMIN_EMAILS", "")
    assert http.get("/admin/health").status_code == 404


def test_signed_out_is_refused(api):
    http, _, _, _, _ = api
    http.cookies.clear()
    assert http.get("/admin/health").status_code == 401


def test_the_user_list_carries_no_credential_material(api):
    """Not an AI account's label even, nor anything sealed."""
    from libs.db.models import UserSecret

    http, db, operator, ordinary, sign_in = api
    insert(db, UserSecret(user_id=ordinary.id, kind="claude_api", ciphertext=b"x", nonce=b"n",
                          label="Claude API key", active=True))
    sign_in(http, operator)

    body = http.get("/admin/users").text
    assert "Claude API key" not in body
    assert "ciphertext" not in body and "nonce" not in body


def test_the_user_list_shows_what_an_operator_actually_needs(api):
    http, _, operator, ordinary, sign_in = api
    sign_in(http, operator)

    rows = {row["email"]: row for row in http.get("/admin/users").json()["users"]}
    assert ordinary.email in rows
    # No `model_version`: the model it reported went with the model (ROADMAP Phase 8).
    assert {"status", "telegram", "onboarded", "last_active_at"} <= set(rows[ordinary.email])


def test_the_account_list_reports_how_long_an_account_has_been_quiet(api):
    """The replacement for automatic dormancy: a number a person reads, then decides (ROADMAP 6c).

    Nothing pauses a quiet account any more, so this column is the only thing standing between an
    abandoned account and it harvesting forever. If it stops being reported, the decision it exists
    for cannot be made.
    """
    from datetime import timedelta

    http, db, operator, ordinary, sign_in = api
    _change(db, ordinary, last_active_at=now() - timedelta(days=45))
    sign_in(http, operator)

    rows = {row["email"]: row for row in http.get("/admin/users").json()["users"]}
    assert rows[ordinary.email]["last_active_at"] is not None, (
        "an account quiet for 45 days reports nothing, so an operator cannot see it is quiet")
    # An account that has never signed in or tapped anything reports null rather than a date, which
    # is the case an operator most wants to see — a signup that never came back.
    assert rows[operator.email]["last_active_at"] is None


# ── operator access, granted from the panel ───────────────────────────────────────────────────

def test_the_flag_grants_access_without_a_deploy(api):
    """`ADMIN_EMAILS` is the bootstrap and needs a container restart to change."""
    http, _db, operator, ordinary, sign_in = api
    sign_in(http, ordinary)
    assert http.get("/admin/users").status_code == 404, "not an operator yet"

    sign_in(http, operator)
    assert http.put(f"/admin/users/{ordinary.id}/admin", json={"is_admin": True}).status_code == 200

    sign_in(http, ordinary)
    assert http.get("/admin/users").status_code == 200


def test_an_operator_cannot_change_their_own_access(api):
    """The one click that could lock somebody out of their own platform."""
    http, _db, operator, _ordinary, sign_in = api
    sign_in(http, operator)

    refused = http.put(f"/admin/users/{operator.id}/admin", json={"is_admin": False})
    assert refused.status_code == 400
    assert "your own" in refused.json()["detail"]


def test_the_env_var_still_wins_over_a_revoked_flag(api):
    """A flag cannot revoke the bootstrap. The two are ORed, and that direction is the safety."""
    http, db, operator, _ordinary, sign_in = api
    _change(db, operator, is_admin=False)

    sign_in(http, operator)
    assert http.get("/admin/users").status_code == 200


# ── deletion, which has no undo ───────────────────────────────────────────────────────────────

def test_deleting_requires_the_address_typed_correctly(api):
    """Checked on the server, not only in the browser."""
    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)

    wrong = http.request("DELETE", f"/admin/users/{ordinary.id}",
                         json={"email": "not-their-address@example.com"})
    assert wrong.status_code == 400
    assert "does not match" in wrong.json()["detail"]
    assert get(db, User, ordinary.id) is not None, "a mistyped confirmation deleted an account"


def test_deleting_takes_the_account_and_everything_belonging_to_it(api):
    """Every collection that names an owner, rather than a list somebody has to remember to extend."""
    from libs.db.models import (
        MODELS,
        CorpusJob,
        QueueJob,
        UserConfig,
        UserJobState,
        UserProfileFile,
        UserSecret,
    )

    http, db, operator, ordinary, sign_in = api
    # Profile files the setup agents wrote for an account before 2026-11-10: kept, so deleted with it.
    insert(db, UserProfileFile(user_id=ordinary.id, name="context.md", content="# Ordinary", kind="context"))
    job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer"))
    insert(db, UserJobState(user_id=ordinary.id, job_id=job.id, applied_at=now()))
    insert(db, UserConfig(user_id=ordinary.id, config={"sections": []}))
    insert(db, QueueJob(lane="docgen", user_id=ordinary.id))
    # A credential, deleted without the platform key: nothing needs reading to be destroyed.
    insert(db, UserSecret(user_id=ordinary.id, kind="openai_api", ciphertext=b"x", nonce=b"n"))
    assert db[UserProfileFile.COLLECTION].count_documents({"user_id": ordinary.id}) > 0

    sign_in(http, operator)
    ok = http.request("DELETE", f"/admin/users/{ordinary.id}",
                      json={"email": ordinary.email.upper()})
    assert ok.status_code == 200, ok.text

    assert get(db, User, ordinary.id) is None
    left = {model.COLLECTION: db[model.COLLECTION].count_documents({model.OWNER: ordinary.id})
            for model in MODELS if model.OWNER}
    assert not any(left.values()), f"left behind: { {name: n for name, n in left.items() if n} }"
    assert get(db, User, operator.id) is not None, "and nobody else's"
    assert get(db, CorpusJob, job.id) is not None, "the shared corpus is nobody's to take"


def test_an_operator_cannot_delete_themselves(api):
    """The account doing the deleting is the one that would have to clean up afterwards."""
    http, db, operator, _ordinary, sign_in = api
    sign_in(http, operator)

    refused = http.request("DELETE", f"/admin/users/{operator.id}",
                           json={"email": operator.email})
    assert refused.status_code == 400
    assert get(db, User, operator.id) is not None


def test_an_ordinary_user_cannot_delete_anybody(api):
    """404 rather than 403, like every other admin route: probing should not confirm the endpoint."""
    http, db, operator, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.request("DELETE", f"/admin/users/{operator.id}",
                        json={"email": operator.email}).status_code == 404
    assert get(db, User, operator.id) is not None


def test_the_source_list_names_every_board_with_its_window(api):
    """A source nobody has tuned still appears, with the default it is actually running at."""
    http, _db, operator, _ordinary, sign_in = api
    sign_in(http, operator)

    body = http.get("/admin/sources").json()["sources"]
    names = {row["name"] for row in body}

    assert "linkedin" in names and "himalayas" in names
    # A source the platform no longer harvests must not offer a control that changes nothing.
    assert "arbeitnow" not in names, "removed 2026-08-04 — it ignores the search parameter"
    assert all(isinstance(row["hours"], int) and row["hours"] > 0 for row in body)
    assert next(row for row in body if row["name"] == "linkedin")["hours"] == 4



def test_the_source_list_shows_each_setting_as_it_is_set_from_one_read_of_them(api):
    """It asked the database once a number: 85 reads, 2.7 s against Atlas (2026-11-03). Now one read
    of every setting answers them all, and each answer is the one the setting gives on its own."""
    from pymongo import MongoClient, monitoring

    from libs import settings
    from services.api.app import app
    from services.api.deps import get_db

    http, db, operator, _ordinary, sign_in = api
    sign_in(http, operator)
    settings.put(db, "source_freshness_hours", {"himalayas": 7})
    # Adzuna's calls a run, and a value left stored for a source removed since (2026-11-06).
    settings.put(db, "source_queries_per_cycle", {"adzuna": 3, "hiringcafe": 3})
    settings.put(db, "source_min_age_hours", {"jobicy": 11})
    settings.put(db, "ats_companies_per_cycle", 123)

    class Count(monitoring.CommandListener):
        reads = 0

        def started(self, event):
            Count.reads += event.command_name in ("find", "aggregate", "count", "distinct")

        def succeeded(self, event):
            pass

        def failed(self, event):
            pass

    # A client of its own, made with the counter: a listener registered later never sees an existing one.
    counted = MongoClient(os.environ["MONGODB_URI"], uuidRepresentation="standard", tz_aware=True,
                          event_listeners=[Count()])[db.name]
    app.dependency_overrides[get_db] = lambda: counted
    try:
        body = http.get("/admin/sources").json()
    finally:
        app.dependency_overrides.pop(get_db)
        counted.client.close()
    asked = Count.reads
    assert asked > 0, "the counter saw nothing: it is not measuring the route"

    rows = {row["name"]: row for row in body["sources"]}
    assert (rows["himalayas"]["hours"], rows["adzuna"]["queries_per_cycle"]) == (7, 3)
    assert "hiringcafe" not in rows
    assert {name for name, row in rows.items() if row["queries_per_cycle"] is not None} == {"adzuna"}, (
        "only the source metered by the call has calls to set: no board is asked for a word")
    assert rows["jobicy"]["min_age_hours"] == 11 and body["ats"]["companies_per_cycle"] == 123
    for name, row in rows.items():
        assert row["hours"] == settings.freshness_hours(db, name)
        assert row["queries_per_cycle"] == settings.queries_per_cycle(db, name)
        assert row["results_per_query"] == settings.results_per_query(db, name)
        assert row["pages_per_cycle"] == settings.pages_per_cycle(db, name)
    assert asked <= 10, f"{asked} reads for one page of settings"

# ── per-user ceilings ─────────────────────────────────────────────────────────────────────────

def test_a_limit_is_absent_default_or_unlimited_and_they_are_three_things(api):
    """Absent, `null` and a number mean different things."""
    from libs import limits

    _http, db, _operator, ordinary, _sign_in = api

    assert limits.resolve(ordinary, "items_per_run") == limits.BY_KEY["items_per_run"].default

    limits.set_limit(db, ordinary.id, "notifications_per_day", None)
    assert limits.resolve(get(db, User, ordinary.id), "notifications_per_day") is None, (
        "null means unlimited")

    limits.set_limit(db, ordinary.id, "notifications_per_day", 40)
    assert limits.resolve(get(db, User, ordinary.id), "notifications_per_day") == 40


def test_the_send_gap_cannot_be_removed(api):
    """It is Telegram's rule, not ours: 20 messages a minute to a channel, 429 above it."""
    import pytest as _pytest

    from libs import limits

    _http, db, _operator, ordinary, _sign_in = api

    with _pytest.raises(ValueError):
        limits.set_limit(db, ordinary.id, "send_gap_seconds", None)


def test_an_operator_sets_limits_through_the_panel_and_they_persist(api):
    """The whole point: changing a ceiling is a write, not a deploy."""
    from libs import limits

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)

    body = http.put(f"/admin/users/{ordinary.id}/limits/items_per_run", json={"value": 25})
    assert body.status_code == 200, body.text
    assert body.json()["limits"]["items_per_run"] == 25
    assert limits.resolve(get(db, User, ordinary.id), "items_per_run") == 25, "what the notifier reads"

    unlimited = http.put(f"/admin/users/{ordinary.id}/limits/notifications_per_day",
                         json={"value": None})
    assert unlimited.json()["limits"]["notifications_per_day"] is None

    refused = http.put(f"/admin/users/{ordinary.id}/limits/send_gap_seconds", json={"value": None})
    assert refused.status_code == 400

    unknown = http.put(f"/admin/users/{ordinary.id}/limits/not_a_limit", json={"value": 1})
    assert unknown.status_code == 404


def test_an_operator_can_set_their_own_limits(api):
    """Unlike operator access and deletion, there is no self-guard here."""
    http, _db, operator, _ordinary, sign_in = api
    sign_in(http, operator)

    body = http.put(f"/admin/users/{operator.id}/limits/items_per_run", json={"value": 50})
    assert body.status_code == 200
    assert body.json()["limits"]["items_per_run"] == 50


def test_an_ordinary_user_cannot_change_any_limit(api):
    """They are the operator's, entirely."""
    http, _db, _operator, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.put(f"/admin/users/{ordinary.id}/limits/items_per_run",
                    json={"value": 999}).status_code == 404
    assert http.get("/admin/limits").status_code == 404


def test_granting_admin_also_shows_the_menu_item(api, monkeypatch):
    """The toggle has to move both halves."""
    # `ordinary` is nobody's bootstrap operator, which is the point.
    monkeypatch.setenv("ADMIN_EMAILS", "")
    http, db, _operator, user, sign_in = api
    sign_in(http, user)

    _change(db, user, is_admin=False)
    assert http.get("/dashboard").json()["is_admin"] is False
    # 404 rather than 403, matching the rest of the panel: a non-admin is not told the endpoint exists at all.
    assert http.get("/admin/users").status_code == 404

    _change(db, user, is_admin=True)
    # Still mid-onboarding here, which is the branch that used to omit the key entirely.
    assert http.get("/dashboard").json()["is_admin"] is True, "the menu must appear"
    assert http.get("/admin/users").status_code == 200, "and lead somewhere"


def test_the_corpus_browser_reports_what_is_actually_in_there(api):
    """The only way to see the corpus was SQL, so nobody looked."""
    from libs.db.models import CorpusJob

    http, db, operator, _ordinary, sign_in = api
    sign_in(http, operator)
    for i in range(3):
        insert(db, CorpusJob(content_key=f"c-{i}", title="Graduate Mechanical Engineer",
                             company="Acme", location="United Kingdom", source="greenhouse",
                             url=f"https://example.com/{i}", is_remote=False, first_seen_at=now()))
    insert(db, CorpusJob(content_key="c-other", title="Backend Engineer", company="Other",
                         location="Remote", source="remotive", url="https://example.com/x",
                         is_remote=True, first_seen_at=now()))

    everything = http.get("/admin/corpus").json()
    assert everything["total"] == 4
    assert {(row["source"], row["count"]) for row in everything["sources"]} == {
        ("greenhouse", 3), ("remotive", 1)}
    # No per-user scores: this is the shared table and a score belongs to an account.
    assert all("score" not in job for job in everything["jobs"])

    searched = http.get("/admin/corpus?q=mechanical").json()
    assert searched["total"] == 3
    assert all("Mechanical" in job["title"] for job in searched["jobs"])


# ── discarding dead letters (ROADMAP 6af) ─────────────────────────────────────────────────────


def _dead_job(db, user, lane="notify", error="RuntimeError: Bad Gateway"):
    from libs.db.models import QueueJob

    return insert(db, QueueJob(lane=lane, user_id=user.id, payload={}, state="dead", attempts=5,
                               last_error=error))


def test_discarding_dead_letters_clears_the_alert_they_caused(api):
    """An alert that cannot be cleared is one an operator learns to ignore."""
    from libs.ops import evaluate, platform_health

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    _dead_job(db, ordinary)
    _dead_job(db, ordinary)

    assert any(alert.key == "dead_letter:notify" for alert in evaluate(platform_health(db)))

    body = http.request("DELETE", "/admin/queue/dead?lane=notify").json()

    assert body == {"status": "discarded", "count": 2, "lane": "notify"}
    assert not any(alert.key.startswith("dead_letter")
                   for alert in evaluate(platform_health(db))), (
        "the alert must go because its cause is gone, not because a banner was hidden")


def test_discarding_one_lane_leaves_the_others_alone(api):
    """A lane is named so that clearing a known Telegram outage does not also erase an unread failure in."""
    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    _dead_job(db, ordinary, lane="notify")
    _dead_job(db, ordinary, lane="docgen")

    body = http.request("DELETE", "/admin/queue/dead?lane=notify").json()

    assert body["count"] == 1
    lanes = {row.lane: row.dead for row in _lane_health(db)}
    assert lanes["notify"] == 0
    assert lanes["docgen"] == 1


def _lane_health(db):
    from libs.ops.health import lane_health

    return lane_health(db)


def test_the_failure_is_written_to_the_log_before_the_row_goes(api, caplog):
    """The row goes, the evidence does not."""
    import logging

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    _dead_job(db, ordinary, error="RuntimeError: Bad Gateway")

    with caplog.at_level(logging.WARNING):
        http.request("DELETE", "/admin/queue/dead?lane=notify")

    assert any("Bad Gateway" in record.getMessage() for record in caplog.records), (
        "the last error must reach the log, because the row that held it is now gone")


def test_an_ordinary_user_cannot_discard_anything(api):
    """Same rule as every other admin route, and worth pinning on a destructive one."""
    http, db, _operator, ordinary, sign_in = api
    sign_in(http, ordinary)
    _dead_job(db, ordinary)

    assert http.request("DELETE", "/admin/queue/dead").status_code == 404
    assert db["jobs"].count_documents({"state": "dead"}) == 1, "and nothing was discarded"


def test_the_live_pipeline_pushes_a_usable_frame(api):
    """A stream that connects and never sends is indistinguishable from a healthy quiet platform."""
    import asyncio
    import json

    from fastapi import Request

    from services.api.admin_routes import pipeline_stream

    _http, db, operator, _, _sign_in = api

    async def one_frame():
        async def receive():
            # Never resolves.
            await asyncio.Event().wait()

        request = Request({"type": "http", "method": "GET", "path": "/admin/pipeline/stream",
                           "headers": [], "query_string": b""}, receive)
        response = await pipeline_stream(request, db=db, admin=operator, hours=1)
        assert response.media_type == "text/event-stream"
        # Proxies buffer an event stream into uselessness without this.
        assert response.headers["x-accel-buffering"] == "no"
        async for chunk in response.body_iterator:
            return chunk

    frame = asyncio.run(one_frame())

    assert frame.startswith("data: ")
    assert frame.endswith("\n\n"), "an unterminated event sits in the browser's buffer forever"
    payload = json.loads(frame[len("data: "):])
    assert [stage["key"] for stage in payload["stages"]] == ["gather", "describe", "deliver"]
    assert "lanes" in payload


def test_the_live_pipeline_is_not_public(api):
    """The same guard every admin route has."""
    http, _, _, ordinary, sign_in = api
    sign_in(http, ordinary)

    # Authorisation is decided before the first frame.
    assert http.get("/admin/pipeline/stream").status_code == 404


# ── what is found where (owner, 2026-11-04) ───────────────────────────────────────────────────

def test_the_jobs_found_in_each_country_are_charted_per_run_per_hour_and_per_day(api):
    """Counted as each posting is stored, by its run and its country, and kept when the postings are
    archived: the chart reaches back further than the corpus does. A posting seen again is not new. Per run
    up to half a day, per hour to two weeks, per day beyond, and from the first count: a zero before it
    would be a count nobody took."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob, CountryCount

    http, db, operator, _, sign_in = api
    sign_in(http, operator)
    corpus = CorpusRepository(db)
    for n, where in enumerate(["Berlin, Germany", "Munich, Germany", "Paris, France", "Remote"]):
        corpus.add(url=f"https://example.com/{n}", source="linkedin", title="Nurse", location=where)
    corpus.add(url="https://example.com/0", source="linkedin", title="Nurse", location="Berlin, Germany")
    db[CorpusJob.COLLECTION].delete_many({})  # archived: the count stays
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    began = hour - timedelta(hours=30)  # counted per hour then, under an id without minutes
    insert(db, CountryCount(id=f"{began:%Y-%m-%dT%H}|DE", hour=began, country="DE", jobs=5))

    charts = {hours: http.get("/admin/countries", params={"hours": hours}).json()
              for hours in (2, 24, 168, 720)}

    assert {hours: chart["bucket_minutes"] for hours, chart in charts.items()} == {
        2: 15, 24: 60, 168: 60, 720: 1440}
    assert len(charts[2]["starts"]) == 8 and len(charts[24]["starts"]) == 24
    assert len(charts[168]["starts"]) == 31, "from the first count, not a week of zeros"
    assert len(charts[720]["starts"]) == (hour.date() - began.date()).days + 1
    for hours in (2, 24):
        assert [(each["name"], each["total"]) for each in charts[hours]["countries"]] == [
            ("Germany", 2), ("France", 1), ("Not known", 1)]
    germany = charts[168]["countries"][0]
    assert (germany["name"], germany["counts"][0], germany["total"]) == ("Germany", 5, 7)
    assert charts[720]["countries"][0]["total"] == 7


def test_a_job_is_counted_in_the_run_it_was_found_in(db):
    """Jobs arrive a run at a time, so the chart's finest count is a run's."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob, CountryCount
    from libs.ops.countries import floor
    from libs.settings import PLATFORM_TICK_MIN

    corpus = CorpusRepository(db)
    job_id = corpus.add(url="https://example.com/1", source="linkedin", title="Nurse",
                        location="Berlin, Germany")
    found = db[CorpusJob.COLLECTION].find_one({"_id": job_id})["first_seen_at"].replace(tzinfo=UTC)

    (row,) = db[CountryCount.COLLECTION].find({})
    assert (row["country"], row["source"], row["jobs"]) == ("DE", "linkedin", 1)
    run = row["hour"].replace(tzinfo=UTC)
    assert run == floor(found, PLATFORM_TICK_MIN)
    assert run.minute % PLATFORM_TICK_MIN == 0 and run <= found < run + timedelta(minutes=PLATFORM_TICK_MIN)


def test_each_range_adds_the_runs_up_into_its_own_buckets(db):
    """The same counts, per run over an hour, per hour over a day, per four hours, per day over a month."""
    from libs.db.models import CountryCount
    from libs.ops.countries import by_country

    now = datetime(2026, 11, 5, 13, 7, tzinfo=UTC)
    for at, jobs in ((datetime(2026, 11, 4, 9, 30, tzinfo=UTC), 4),
                     (datetime(2026, 11, 5, 12, 45, tzinfo=UTC), 2),
                     (datetime(2026, 11, 5, 13, 0, tzinfo=UTC), 3)):
        insert(db, CountryCount(id=f"{at:%Y-%m-%dT%H:%M}|DE", hour=at, country="DE", jobs=jobs))

    hour, day, month = (by_country(db, hours=hours, now=now) for hours in (1, 24, 720))
    four = by_country(db, hours=24, minutes=240, now=now)

    assert [start.strftime("%H:%M") for start in hour["starts"]] == ["12:15", "12:30", "12:45", "13:00"]
    assert hour["countries"][0]["counts"] == [0, 0, 2, 3]
    assert day["countries"][0]["counts"][-2:] == [2, 3] and day["countries"][0]["total"] == 5
    assert month["countries"][0]["counts"] == [4, 5], "from the first count's day, a sum a day"
    assert [start.strftime("%d %H:%M") for start in four["starts"][-2:]] == ["05 08:00", "05 12:00"]
    assert four["countries"][0]["counts"][-2:] == [0, 5] and four["countries"][0]["total"] == 5


def test_what_the_chart_shows_and_what_one_point_stands_for_are_picked_apart(api):
    """Everything counted, from the first count on; and a point a run, half an hour, an hour, two, four,
    eight or twelve hours, or a day over any range, as long as its points fit across the chart (owner,
    2026-11-06, -10)."""
    from libs.db.models import CountryCount
    from libs.ops.countries import by_country

    http, db, operator, _, sign_in = api
    sign_in(http, operator)
    now = datetime(2026, 11, 5, 13, 7, tzinfo=UTC)
    for at, jobs in ((datetime(2026, 10, 20, 9, 30, tzinfo=UTC), 4),
                     (datetime(2026, 11, 5, 12, 45, tzinfo=UTC), 2),
                     (datetime(2026, 11, 5, 13, 0, tzinfo=UTC), 3)):
        insert(db, CountryCount(id=f"{at:%Y-%m-%dT%H:%M}|DE", hour=at, country="DE", jobs=jobs))

    assert by_country(db, hours=24, now=now)["steps"] == [15, 30, 60, 120, 240, 480, 720, 1440]
    everything = by_country(db, hours=None, now=now)
    assert everything["bucket_minutes"] == 1440
    assert everything["steps"] == [30, 60, 120, 240, 480, 720, 1440], "a run a point: 1,552 of them"
    assert everything["starts"][0] == datetime(2026, 10, 20, tzinfo=UTC) and len(everything["starts"]) == 17
    hourly = by_country(db, hours=None, minutes=60, now=now)
    assert (len(hourly["starts"]), hourly["countries"][0]["total"]) == (389, 9), "16 days and 4 hours, all 9"

    a_day_by_run = by_country(db, hours=24, minutes=15, now=now)
    assert (a_day_by_run["bucket_minutes"], len(a_day_by_run["starts"])) == (15, 96)
    assert a_day_by_run["countries"][0]["counts"][-2:] == [2, 3]
    a_week_by_day = by_country(db, hours=168, minutes=1440, now=now)
    assert (a_week_by_day["bucket_minutes"], a_week_by_day["countries"][0]["counts"]) == (1440, [0] * 6 + [5])
    a_month_by_run = by_country(db, hours=720, minutes=15, now=now)
    assert a_month_by_run["bucket_minutes"] == 1440
    assert a_month_by_run["steps"] == [60, 120, 240, 480, 720, 1440], "not offered: 2,880 points"

    asked = http.get("/admin/countries", params={"minutes": 1440}).json()
    assert (asked["bucket_minutes"], asked["starts"][0][:10], asked["countries"][0]["total"]) == (
        1440, "2026-10-20", 9)
    assert http.get("/admin/countries", params={"hours": 24, "minutes": 15}).json()["bucket_minutes"] == 15


def test_the_chart_counts_the_sources_picked_and_lists_every_source(api):
    """Every job board counted at first; one left out takes its jobs out of every country, and is still
    listed with its own, as is a board that found nothing. Counts kept before sources were are a source of
    their own, "Not recorded", that can be left out too (owner, 2026-11-10)."""
    from libs.db.models import CountryCount
    from libs.settings import HARVEST_SOURCES

    http, db, operator, _, sign_in = api
    sign_in(http, operator)
    run = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    for country, source, jobs in (("DE", "linkedin", 5), ("DE", "greenhouse", 2), ("FR", "greenhouse", 1)):
        insert(db, CountryCount(id=f"{run:%Y-%m-%dT%H:%M}|{country}|{source}", hour=run, country=country,
                                source=source, jobs=jobs))
    db[CountryCount.COLLECTION].insert_one({"_id": f"{run:%Y-%m-%dT%H:%M}|DE", "hour": run, "country": "DE",
                                            "jobs": 3})

    def chart(*without: str) -> tuple[list, dict]:
        body = http.get("/admin/countries", params={"hours": 24, "without": list(without)}).json()
        return ([(each["name"], each["total"]) for each in body["countries"]],
                {each["source"]: each["total"] for each in body["sources"]})

    everything, sources = chart()
    assert everything == [("Germany", 10), ("France", 1)]
    assert sources == {**dict.fromkeys(HARVEST_SOURCES, 0), "linkedin": 5, "greenhouse": 3, None: 3}
    assert chart("greenhouse") == ([("Germany", 8)], sources), "gone from every country, still listed"
    assert chart("greenhouse", "")[0] == [("Germany", 5)], "the counts before sources, left out too"
    assert chart(*HARVEST_SOURCES, "")[0] == []


def test_counts_kept_before_sources_are_split_by_the_jobs_the_corpus_still_holds(db):
    """Once, at the deploy's schema step: a count without a source is split by the sources of the jobs it
    counted, a run's or an hour's (before 2026-11-05) alike. What the corpus no longer holds stays under
    none, every count keeps its total, and a second time does nothing."""
    from libs.corpus.repository import CorpusRepository
    from libs.db.models import CorpusJob, CountryCount
    from libs.ops.countries import split_by_source

    corpus = CorpusRepository(db)
    for n, source in enumerate(("linkedin", "linkedin", "greenhouse")):
        corpus.add(url=f"https://example.com/de{n}", source=source, title="Nurse", location="Berlin, Germany")
    hour_ago = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=30)
    paris = corpus.add(url="https://example.com/fr", source="lever", title="Nurse", location="Paris, France")
    db[CorpusJob.COLLECTION].update_one({"_id": paris},
                                        {"$set": {"first_seen_at": hour_ago + timedelta(minutes=40)}})
    # As they were kept before sources were: a run's count of four, one of them archived since, and an hour's.
    (run,) = {row["hour"] for row in db[CountryCount.COLLECTION].find({"country": "DE"})}
    db[CountryCount.COLLECTION].delete_many({})
    db[CountryCount.COLLECTION].insert_many([
        {"_id": f"{run:%Y-%m-%dT%H:%M}|DE", "hour": run, "country": "DE", "jobs": 4},
        {"_id": f"{hour_ago:%Y-%m-%dT%H}|FR", "hour": hour_ago, "country": "FR", "jobs": 1}])

    assert split_by_source(db) == 2

    rows = {(row["country"], row["source"]): row["jobs"] for row in db[CountryCount.COLLECTION].find()}
    assert rows == {("DE", "linkedin"): 2, ("DE", "greenhouse"): 1, ("DE", None): 1, ("FR", "lever"): 1}
    assert split_by_source(db) == 0


def test_the_chart_says_how_many_linkedin_jobs_still_wait_for_their_text(api):
    """A number that grows run after run is a pass that cannot keep up: the proxy needs more exits."""
    from libs.corpus.repository import CorpusRepository

    http, db, operator, _, sign_in = api
    sign_in(http, operator)
    corpus = CorpusRepository(db)
    for n in range(3):
        corpus.add(url=f"https://www.linkedin.com/jobs/view/{n}", source="linkedin", title="Nurse",
                   location="Berlin, Germany")
    corpus.set_description(corpus.add(url="https://www.linkedin.com/jobs/view/9", source="linkedin",
                                      title="Nurse", location="Berlin, Germany"), "Ward nursing. " * 40)

    assert http.get("/admin/countries").json()["waiting"] == 3
