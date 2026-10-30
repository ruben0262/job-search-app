"""The admin endpoints, and who is refused them."""
from __future__ import annotations

import os
import uuid

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

    operator = insert(db, User(email=ADMIN, password_hash="x", status="active"))
    ordinary = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))

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


def test_an_account_page_shows_what_happened_to_it_and_no_model(api):
    """Applications and deliveries, in numbers. The model history went with the model."""
    from libs.db.models import CorpusJob, UserJobState

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer"))
    insert(db, UserJobState(user_id=ordinary.id, job_id=job.id, applied_at=now(), notified_at=now()))

    body = http.get(f"/admin/users/{ordinary.id}").json()
    assert body["user"]["email"] == ordinary.email
    assert body["counts"] == {"applied": 1, "notified": 1}
    assert "models" not in body


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
    """Not even `last_4`."""
    from libs.db.models import UserSecret

    http, db, operator, ordinary, sign_in = api
    insert(db, UserSecret(user_id=ordinary.id, kind="claude_api", ciphertext=b"x", nonce=b"n",
                          last_4="9999"))
    sign_in(http, operator)

    body = http.get("/admin/users").text
    assert "9999" not in body
    assert "ciphertext" not in body and "last_4" not in body


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
    from libs.profiles.repository import ProfileFiles

    http, db, operator, ordinary, sign_in = api
    ProfileFiles(db, ordinary.id).seed()
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


def test_operator_keywords_are_added_listed_and_removed(api):
    """Seeding the corpus ahead of the users who will need it."""
    http, _db, operator, _ordinary, sign_in = api
    sign_in(http, operator)

    created = http.post("/admin/keywords",
                        json={"keyword": "  Stress   Engineer ", "location": "united_kingdom",
                              "note": "seeding mech eng"})
    assert created.status_code == 201
    assert created.json()["keyword"] == "stress engineer", "whitespace and case are normalised"

    # Adding it again is somebody unsure whether it took, not a request for two query stems.
    again = http.post("/admin/keywords",
                      json={"keyword": "stress engineer", "location": "united_kingdom"})
    assert again.json()["created"] is False

    listed = http.get("/admin/keywords").json()
    assert any(row["keyword"] == "stress engineer" for row in listed["operator"])

    removed = http.delete(f"/admin/keywords/{created.json()['id']}")
    assert removed.status_code == 200
    assert not http.get("/admin/keywords").json()["operator"]


def test_a_users_own_search_terms_are_shown_but_never_deletable(api):
    """Their feed is built from these."""
    from libs.db.models import UserConfig

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    insert(db, UserConfig(user_id=ordinary.id,
                          config={"sections": [{"keywords": ["quantum welder"],
                                                "locations": ["europe"], "platform": "all"}]}))

    listed = http.get("/admin/keywords").json()
    theirs = [row for row in listed["users"] if row["keyword"] == "quantum welder"]
    assert theirs, "an operator should be able to see what a user searches for"
    assert theirs[0]["deletable"] is False
    assert ordinary.email in theirs[0]["accounts"]


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
