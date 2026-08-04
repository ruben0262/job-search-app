"""The admin endpoints, and who is refused them.

The access rule is the whole of what is worth testing here: an operator view that leaks is worse
than no operator view, because it hands a stranger every account's state at once. So the tests are
about who *cannot* read it — including the case that looks like a bug and is not, the 404.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")

ADMIN = "operator@example.com"


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"
    os.environ["ADMIN_EMAILS"] = ADMIN

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import User
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    operator = User(email=ADMIN, password_hash="x", status="active")
    ordinary = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add_all([operator, ordinary])
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session

    def sign_in(http, user):
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")

    try:
        with TestClient(app, base_url="https://testserver") as http:
            yield http, session, operator, ordinary, sign_in
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)
        os.environ.pop("ADMIN_EMAILS", None)


def test_an_operator_sees_the_platform_and_its_alerts(api):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    body = http.get("/admin/health").json()
    assert {"lanes", "corpus_rows", "alerts", "summary"} <= set(body)
    assert isinstance(body["alerts"], list)


def test_an_ordinary_user_gets_a_404_not_a_403(api):
    """A 403 confirms the endpoint exists and that somebody is worth phishing for access to it.
    There is no legitimate client that finds these routes by probing."""
    http, _, _, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.get("/admin/health").status_code == 404
    assert http.get("/admin/users").status_code == 404


def test_admin_is_an_environment_variable_not_a_column(api):
    """Granting admin is a deploy, which is the friction it should have. A boolean column would be
    one UPDATE away from privilege escalation, audited in the database it grants access to."""
    http, session, operator, _, sign_in = api
    sign_in(http, operator)
    assert http.get("/admin/health").status_code == 200

    os.environ["ADMIN_EMAILS"] = "somebody-else@example.com"
    session.flush()
    assert http.get("/admin/health").status_code == 404


def test_no_admin_configured_means_nobody_is_an_admin(api):
    """An empty variable must not mean "everybody" — that is the misconfiguration that opens the
    console on the first deploy where somebody forgot to set it."""
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    os.environ["ADMIN_EMAILS"] = ""
    assert http.get("/admin/health").status_code == 404


def test_signed_out_is_refused(api):
    http, _, _, _, _ = api
    http.cookies.clear()
    assert http.get("/admin/health").status_code == 401


def test_the_user_list_carries_no_credential_material(api):
    """Not even `last_4`. An operator does not need it to answer any question they have, and a
    support view that shows a fragment of a key leaks one over somebody's shoulder."""
    from libs.db.models import UserSecret

    http, session, operator, ordinary, sign_in = api
    session.add(UserSecret(user_id=ordinary.id, kind="claude_api", ciphertext=b"x", nonce=b"n",
                           last_4="9999"))
    session.flush()
    sign_in(http, operator)

    body = http.get("/admin/users").text
    assert "9999" not in body
    assert "ciphertext" not in body and "last_4" not in body


def test_the_user_list_shows_what_an_operator_actually_needs(api):
    http, _, operator, ordinary, sign_in = api
    sign_in(http, operator)

    rows = {row["email"]: row for row in http.get("/admin/users").json()["users"]}
    assert ordinary.email in rows
    assert {"status", "telegram", "model_version", "onboarded"} <= set(rows[ordinary.email])


# ── operator access, granted from the panel ───────────────────────────────────────────────────

def test_the_flag_grants_access_without_a_deploy(api):
    """`ADMIN_EMAILS` is the bootstrap and needs a container restart to change, which makes "give
    this person access" a deploy. The row is how it is granted at the speed the question is asked."""
    http, session, operator, ordinary, sign_in = api
    sign_in(http, ordinary)
    assert http.get("/admin/users").status_code == 404, "not an operator yet"

    sign_in(http, operator)
    assert http.put(f"/admin/users/{ordinary.id}/admin", json={"is_admin": True}).status_code == 200
    session.flush()

    sign_in(http, ordinary)
    assert http.get("/admin/users").status_code == 200


def test_an_operator_cannot_change_their_own_access(api):
    """**The one click that could lock somebody out of their own platform.** `ADMIN_EMAILS` is the
    only route back in, so an operator not named in it who revoked their own row would need a
    redeploy to undo it."""
    http, _session, operator, _ordinary, sign_in = api
    sign_in(http, operator)

    refused = http.put(f"/admin/users/{operator.id}/admin", json={"is_admin": False})
    assert refused.status_code == 400
    assert "your own" in refused.json()["detail"]


def test_the_env_var_still_wins_over_a_revoked_flag(api):
    """A flag cannot revoke the bootstrap. The two are ORed, and that direction is the safety."""
    http, session, operator, _ordinary, sign_in = api
    operator.is_admin = False
    session.flush()

    sign_in(http, operator)
    assert http.get("/admin/users").status_code == 200


# ── deletion, which has no undo ───────────────────────────────────────────────────────────────

def test_deleting_requires_the_address_typed_correctly(api):
    """**Checked on the server, not only in the browser.** A confirmation enforced in the UI alone
    is one a mis-aimed script skips entirely, and this destroys a person's profile, labels,
    documents and model with no undo."""
    from libs.db.models import User

    http, session, operator, ordinary, sign_in = api
    sign_in(http, operator)

    wrong = http.request("DELETE", f"/admin/users/{ordinary.id}",
                         json={"email": "not-their-address@example.com"})
    assert wrong.status_code == 400
    assert "does not match" in wrong.json()["detail"]
    assert session.get(User, ordinary.id) is not None, "a mistyped confirmation deleted an account"


def test_deleting_takes_the_account_and_everything_belonging_to_it(api):
    """Cascade from the row rather than a list of tables somebody has to remember to extend."""
    from libs.db.models import User, UserProfileFile
    from libs.profiles.repository import ProfileFiles

    http, session, operator, ordinary, sign_in = api
    ProfileFiles(session, ordinary.id).seed()
    session.flush()
    assert session.query(UserProfileFile).filter_by(user_id=ordinary.id).count() > 0

    sign_in(http, operator)
    ok = http.request("DELETE", f"/admin/users/{ordinary.id}",
                      json={"email": ordinary.email.upper()})
    assert ok.status_code == 200, ok.text
    session.flush()
    session.expire_all()

    assert session.get(User, ordinary.id) is None
    assert session.query(UserProfileFile).filter_by(user_id=ordinary.id).count() == 0


def test_an_operator_cannot_delete_themselves(api):
    """The account doing the deleting is the one that would have to clean up afterwards."""
    from libs.db.models import User

    http, session, operator, _ordinary, sign_in = api
    sign_in(http, operator)

    refused = http.request("DELETE", f"/admin/users/{operator.id}",
                           json={"email": operator.email})
    assert refused.status_code == 400
    assert session.get(User, operator.id) is not None


def test_an_ordinary_user_cannot_delete_anybody(api):
    """404 rather than 403, like every other admin route: probing should not confirm the endpoint."""
    from libs.db.models import User

    http, session, operator, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.request("DELETE", f"/admin/users/{operator.id}",
                        json={"email": operator.email}).status_code == 404
    assert session.get(User, operator.id) is not None


def test_the_source_list_names_every_board_with_its_window(api):
    """A source nobody has tuned still appears, with the default it is actually running at —
    otherwise the panel omits exactly the boards most likely to need attention."""
    http, _session, operator, _ordinary, sign_in = api
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
    """**Absent, `null` and a number mean different things.**

    Absent follows the platform default as that default changes; `null` is an operator choosing no
    ceiling; a number is that number. Storing a huge value for unlimited would collapse the first
    two and make a misconfiguration indistinguishable from an intention.
    """
    from libs import limits

    _http, session, _operator, ordinary, _sign_in = api

    assert limits.resolve(ordinary, "items_per_run") == limits.BY_KEY["items_per_run"].default

    limits.set_limit(session, ordinary.id, "notifications_per_day", None)
    session.flush()
    assert limits.resolve(ordinary, "notifications_per_day") is None, "null means unlimited"

    limits.set_limit(session, ordinary.id, "notifications_per_day", 40)
    session.flush()
    assert limits.resolve(ordinary, "notifications_per_day") == 40


def test_the_send_gap_cannot_be_removed(api):
    """It is Telegram's rule, not ours: 20 messages a minute to a channel, 429 above it. An
    operator who could set it to unlimited could get a user's bot restricted."""
    import pytest as _pytest

    from libs import limits

    _http, session, _operator, ordinary, _sign_in = api

    with _pytest.raises(ValueError):
        limits.set_limit(session, ordinary.id, "send_gap_seconds", None)


def test_an_operator_sets_limits_through_the_panel_and_they_persist(api):
    """The whole point: changing a ceiling is an UPDATE, not a deploy."""
    http, session, operator, ordinary, sign_in = api
    sign_in(http, operator)

    body = http.put(f"/admin/users/{ordinary.id}/limits/items_per_run", json={"value": 25})
    assert body.status_code == 200, body.text
    assert body.json()["limits"]["items_per_run"] == 25
    session.flush()

    unlimited = http.put(f"/admin/users/{ordinary.id}/limits/notifications_per_day",
                         json={"value": None})
    assert unlimited.json()["limits"]["notifications_per_day"] is None

    refused = http.put(f"/admin/users/{ordinary.id}/limits/send_gap_seconds", json={"value": None})
    assert refused.status_code == 400

    unknown = http.put(f"/admin/users/{ordinary.id}/limits/not_a_limit", json={"value": 1})
    assert unknown.status_code == 404


def test_an_operator_can_set_their_own_limits(api):
    """Unlike operator access and deletion, there is no self-guard here: raising your own ceiling
    is not a way to lock yourself out, and an operator is exactly who should be able to."""
    http, _session, operator, _ordinary, sign_in = api
    sign_in(http, operator)

    body = http.put(f"/admin/users/{operator.id}/limits/items_per_run", json={"value": 50})
    assert body.status_code == 200
    assert body.json()["limits"]["items_per_run"] == 50


def test_an_ordinary_user_cannot_change_any_limit(api):
    """They are the operator's, entirely. A user who could raise their own delivery ceiling could
    get their own bot rate-limited by Telegram."""
    http, _session, _operator, ordinary, sign_in = api
    sign_in(http, ordinary)

    assert http.put(f"/admin/users/{ordinary.id}/limits/items_per_run",
                    json={"value": 999}).status_code == 404
    assert http.get("/admin/limits").status_code == 404


def test_granting_admin_also_shows_the_menu_item(api, monkeypatch):
    """**The toggle has to move both halves.** `require_admin` reads the column, so granting from
    the panel opened the API immediately -- but the dashboard computed its `is_admin` hint from
    `ADMIN_EMAILS` alone and kept hiding the nav entry. Access with no way to reach it reads, to the
    person granted it, as a toggle that does nothing.
    """
    # `ordinary` is nobody's bootstrap operator, which is the point: everything below has to come
    # from the flag alone.
    monkeypatch.setenv("ADMIN_EMAILS", "")
    http, session, _operator, user, sign_in = api
    sign_in(http, user)

    user.is_admin = False
    session.flush()
    assert http.get("/dashboard").json()["is_admin"] is False
    # 404 rather than 403, matching the rest of the panel: a non-admin is not told the endpoint
    # exists at all.
    assert http.get("/admin/users").status_code == 404

    user.is_admin = True
    session.flush()
    # Still mid-onboarding here, which is the branch that used to omit the key entirely.
    assert http.get("/dashboard").json()["is_admin"] is True, "the menu must appear"
    assert http.get("/admin/users").status_code == 200, "and lead somewhere"


def test_the_corpus_browser_reports_what_is_actually_in_there(api):
    """**The only way to see the corpus was SQL, so nobody looked.**

    That matters more than it sounds: a new account's training data is whatever this shared table
    already holds for their field, and the second account here found 240 usable rows out of 14,359
    because the corpus had accumulated for weeks against one search and hours against theirs.
    """
    from datetime import UTC, datetime

    from libs.db.models import CorpusJob

    http, session, operator, _ordinary, sign_in = api
    sign_in(http, operator)
    for i in range(3):
        session.add(CorpusJob(content_key=f"c-{i}", title="Graduate Mechanical Engineer",
                              company="Acme", location="United Kingdom", source="greenhouse",
                              url=f"https://example.com/{i}", is_remote=False,
                              first_seen_at=datetime.now(UTC)))
    session.add(CorpusJob(content_key="c-other", title="Backend Engineer", company="Other",
                          location="Remote", source="remotive", url="https://example.com/x",
                          is_remote=True, first_seen_at=datetime.now(UTC)))
    session.flush()

    everything = http.get("/admin/corpus").json()
    assert everything["total"] >= 4
    assert any(row["source"] == "greenhouse" for row in everything["sources"])
    # No per-user scores: this is the shared table and a score belongs to an account.
    assert all("score" not in job for job in everything["jobs"])

    searched = http.get("/admin/corpus?q=mechanical").json()
    assert searched["total"] == 3
    assert all("Mechanical" in job["title"] for job in searched["jobs"])


def test_operator_keywords_are_added_listed_and_removed(api):
    """Seeding the corpus ahead of the users who will need it."""
    http, _session, operator, _ordinary, sign_in = api
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
    """**Their feed is built from these.** An operator seeing them is support; an operator able to
    remove them is an outage that looks like a setting. There is no route that can reach them: the
    delete takes a row id from `operator_keywords`, which a user's terms are not in."""
    from libs.db.models import UserConfig

    http, session, operator, ordinary, sign_in = api
    sign_in(http, operator)
    session.add(UserConfig(user_id=ordinary.id,
                           config={"sections": [{"keywords": ["quantum welder"],
                                                 "locations": ["europe"], "platform": "all"}]}))
    session.flush()

    listed = http.get("/admin/keywords").json()
    theirs = [row for row in listed["users"] if row["keyword"] == "quantum welder"]
    assert theirs, "an operator should be able to see what a user searches for"
    assert theirs[0]["deletable"] is False
    assert ordinary.email in theirs[0]["accounts"]
