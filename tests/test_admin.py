"""The admin endpoints, and who is refused them."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.db import get, insert, now
from libs.db.models import Alert, User
from tests.conftest import agreed

pytest.importorskip("fastapi")

ADMIN = "operator@example.com"


@pytest.fixture()
def api(db, monkeypatch, running):
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)

    from tests.conftest import access_cookie

    operator = insert(db, User(email=ADMIN, password_hash="x", subscription_status="active", status="active",
                               **agreed()))
    ordinary = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                               subscription_status="active", status="active", **agreed()))

    def sign_in(http, user):
        http.cookies.set("access_token", access_cookie(db, user.id), path="/")

    # One browser for them all: whatever the test before left it holding is dropped first.
    running.cookies.clear()
    return running, db, operator, ordinary, sign_in


def _change(db, user, **fields) -> None:
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": fields})


def test_an_operator_sees_the_platform_and_its_alerts(api):
    http, _, operator, _, sign_in = api
    sign_in(http, operator)

    body = http.get("/admin/health").json()
    assert {"lanes", "corpus_rows", "alerts", "users_total", "users_active", "checked_at"} <= set(body)
    assert isinstance(body["alerts"], list)
    # And nothing the page does not show. Every active account's address went out with it, the alerts a
    # second time as one text, and a figure of the Latency tab's worked out on each read (2026-11-20).
    assert not {"users", "summary", "latency"} & set(body)


def test_the_account_list_comes_a_page_at_a_time_and_finds_an_account_by_its_address(api):
    """Every account was read whole on each open of Admin, 19 KB each with its profile, and all of them drawn:
    1.9 s and 196 MB at ten thousand, on a copy made of the dev accounts (2026-11-20). A page of them is read,
    the first to join first, with how many there are; and an address typed finds its account on any page."""
    from services.api.admin_routes import USERS_PAGE

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    later = [insert(db, User(email=f"member{at:03}@example.org", password_hash="x", status="active",
                             created_at=now() + timedelta(seconds=at + 1))).email for at in range(USERS_PAGE)]

    first = http.get("/admin/users").json()
    rest = http.get(f"/admin/users?offset={USERS_PAGE}").json()

    assert (first["total"], first["page_size"]) == (USERS_PAGE + 2, USERS_PAGE)
    assert len(first["users"]) == USERS_PAGE
    assert {row["email"] for row in first["users"]} == {operator.email, ordinary.email, *later[:-2]}
    assert [row["email"] for row in rest["users"]] == later[-2:]

    found = http.get("/admin/users?q=MEMBER007").json()
    assert ([row["email"] for row in found["users"]], found["total"]) == (["member007@example.org"], 1)


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
    _change(db, ordinary, last_active_at=now() - timedelta(hours=2))
    insert(db, Alert(user_id=ordinary.id, name="My search", search=True, channels=["bell", "email"]))
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


def test_an_account_page_has_its_list_counted_where_the_jobs_page_has(api, monkeypatch):
    """By the database, as the Jobs page's own counts are (`libs.matching.page`), and never by bringing every
    match here to count it: 5,884 postings and 675 ms for one account on the dev copy, and again for each row
    of the Accounts table the pointer crossed (2026-11-20)."""
    from libs.tenant.repository import TenantRepository

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    TenantRepository(db).save_config(ordinary.id, {"sections": [{"platform": "all", "keywords": ["nurse"]}]})

    def every_match(*_args, **_kwargs):
        raise AssertionError("the account page read every match to count them")

    monkeypatch.setattr("libs.matching.matching_jobs", every_match)

    assert http.get(f"/admin/users/{ordinary.id}").status_code == 200


def test_the_subscriptions_tab_counts_every_account_once_and_lists_the_subscriptions(api):
    """Owner, 2026-11-12: Admin's Subscriptions, who is on which plan and what is about to change. Every
    account in one plan, by the rule the site lets people in by; the list, a payment failed first."""
    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    soon, later = now() + timedelta(days=3), now() + timedelta(days=20)
    kinds = {"paying": {"subscription_status": "active", "period_ends_at": later, "last_paid_amount": 499,
                        "currency": "gbp", "last_paid_at": now()},
             "trial": {"subscription_status": "trialing", "trial_ends_at": soon},
             "late trial": {"subscription_status": "trialing", "trial_ends_at": later, "cancels_at": later},
             "failed": {"subscription_status": "past_due"},
             "lapsed": {"subscription_status": "canceled", "onboarding_completed_at": now()},
             "given": {"free_access_at": now()},
             "picked free": {"plan_chosen_at": now()}, "new": {}}
    made = {name: insert(db, User(email=f"{name.replace(' ', '.')}@example.com", password_hash="x",
                                  status="active", **fields)) for name, fields in kinds.items()}

    body = http.get("/admin/subscriptions").json()

    # The operator is an admin and `ordinary` pays; "new" is still on the plan page; the lapsed and "picked
    # free" are on the free plan.
    assert body["plans"] == {"paying": 2, "trial": 2, "payment_failed": 1, "paused": 0, "free_access": 1,
                             "admin": 1, "choosing": 1, "free": 2}
    assert (body["accounts"], body["members"]) == (10, 6), "two paying, two trials, free access, the admin"
    assert (body["trials_ending"], body["cancelling"], body["joined_30d"]) == (1, 1, 10)
    rows = [row["email"] for row in body["rows"]]
    assert rows[0] == "failed@example.com" and rows[1:3] == ["trial@example.com", "late.trial@example.com"]
    assert set(rows) == {ADMIN, ordinary.email} | {f"{name.replace(' ', '.')}@example.com"
                                                   for name in ("paying", "trial", "late trial", "failed",
                                                                "lapsed", "given")}

    sign_in(http, ordinary)
    assert http.get("/admin/subscriptions").status_code == 404
    sign_in(http, operator)
    detail = http.get(f"/admin/users/{made['picked free'].id}").json()["user"]
    assert detail["plan_chosen_at"] is not None and detail["subscription_id"] is None


def test_the_subscriptions_tab_says_which_plan_each_account_is_on_and_prices_each_at_its_own(
        api, monkeypatch):
    """Owner, 2026-11-16: Free, Starter and Member. Admin says which plan an account is on, and what the
    paying ones bring in a month each at its own plan's price: Starter's counted at the membership's would
    overstate it."""
    from services.api import admin_routes
    from services.api.billing_routes import Plan

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    starter = insert(db, User(email="starter@example.com", password_hash="x", status="active",
                              subscription_status="active", subscription_plan="starter"))
    lapsed = insert(db, User(email="lapsed@example.com", password_hash="x", status="active",
                             subscription_status="canceled"))
    prices = {"member": 499, "starter": 299}
    monkeypatch.setattr(admin_routes, "billing_plan", lambda db, which="member": Plan(
        amount=prices[which], currency="gbp", interval="month", interval_count=1, trial_days=7))

    body = http.get("/admin/subscriptions").json()

    assert (body["members"], body["starters"]) == (3, 1), "the admin, `ordinary` and Starter"
    assert {row["email"]: row["tier"] for row in body["rows"]} == {
        ADMIN: "member", ordinary.email: "member", starter.email: "starter", lapsed.email: "free"}
    assert body["monthly"] == 499 + 299, "`ordinary` pays for the membership, and Starter for Starter"
    assert [http.get(f"/admin/users/{each.id}").json()["user"]["tier"] for each in (starter, lapsed)] == \
        ["starter", "free"]


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
    assert "remotive" not in names, "its terms forbid showing its jobs on another job site (2026-11-22)"
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


def test_the_counts_by_source_read_the_index_and_not_the_postings(api):
    """The corpus browser's thirty days by source, and the pipeline's day, read every posting of the window:
    1.84 million and 40 s a look on server_db, each through the database's memory (2026-11-28)."""
    from libs.db.models import CorpusJob

    http, db, operator, _ordinary, sign_in = api
    sign_in(http, operator)
    for i in range(3):
        insert(db, CorpusJob(title="Engineer", source="greenhouse", url=f"https://example.com/{i}",
                             first_seen_at=now(), described_at=now() if i else None))
    db.command("profile", 2)
    try:
        assert http.get("/admin/corpus").json()["sources"] == [{"source": "greenhouse", "count": 3}]
        found = {row["source"]: row for row in http.get("/admin/pipeline/sources").json()["sources"]}
        db.command("profile", 0)
        counts = [op for op in db["system.profile"].find({"command.aggregate": CorpusJob.COLLECTION})
                  if list(op["command"]["pipeline"][0].get("$match", {})) == ["first_seen_at"]
                  and op["command"]["pipeline"][1].get("$group", {}).get("_id") == "$source"]
    finally:
        db.command("profile", 0)
        db.drop_collection("system.profile")  # the database refuses the next test's emptying of it
    assert (found["greenhouse"]["found"], found["greenhouse"]["with_text"]) == (3, 2)
    assert len(counts) == 2, "one count from each page"
    assert [op["docsExamined"] for op in counts] == [0, 0], "it read the postings to count them"


def test_the_corpus_browser_lists_the_newest_first_and_says_which_have_their_text(api):
    """A job has its text when the platform says so (`described_at`), the one rule its Jobs pages go by. The
    browser had a rule of its own, 400 characters, so a job on people's lists read as waiting for its text."""
    from libs.db.models import CorpusJob

    http, db, operator, _ordinary, sign_in = api
    sign_in(http, operator)
    for title, text, read in (("Waiting", None, None), ("Read", "We build ledgers. " * 14, now())):
        insert(db, CorpusJob(title=title, source="test", url=f"https://example.com/{title}", description=text,
                             described_at=read, first_seen_at=now()))

    listed = http.get("/admin/corpus").json()["jobs"]

    assert [(job["title"], job["described"]) for job in listed] == [("Read", True), ("Waiting", False)]


def test_the_site_goes_on_answering_while_the_live_pipeline_is_counted(api, monkeypatch):
    """Each frame's counts ran on the server's event loop, once a second for as long as a Pipeline tab was
    open: 78 ms of every second in which that worker answered nobody (2026-11-20)."""
    import threading

    from services.api import admin_routes

    http, _db, operator, _, sign_in = api
    sign_in(http, operator)
    counting, release, count = threading.Event(), threading.Event(), admin_routes.pipeline

    def held(*args, **kwargs):
        counting.set()
        release.wait(30)
        return count(*args, **kwargs)

    monkeypatch.setattr(admin_routes, "pipeline", held)
    monkeypatch.setattr(admin_routes, "STREAM_INTERVAL_SECONDS", 0.01)
    stream = threading.Thread(target=http.get, args=("/admin/pipeline/stream",), daemon=True)
    stream.start()
    try:
        assert counting.wait(10), "the premise: the stream got as far as counting a frame"
        answered: list[int] = []
        other = threading.Thread(target=lambda: answered.append(http.get("/health").status_code), daemon=True)
        other.start()
        other.join(5)
        assert answered == [200]
    finally:
        # The stream ends by itself at its next turn, once the frame is let go.
        monkeypatch.setattr(admin_routes, "STREAM_MAX_SECONDS", 0)
        release.set()
        stream.join(30)


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


# ── what each tab is sent, in the shape its handler declares (2026-11-21) ──────────────────────────────────

def test_every_admin_read_says_what_it_answers():
    """The Admin pages kept a copy by hand of what every handler sent, 250 lines of them, because the
    handlers said only "an object": one copy had a part the server had stopped sending. A handler that says
    its shape is where the pages' types come from (`web/src/api-types.ts`), so there is nothing to copy."""
    from services.api.app import app

    said = "application/json"
    reads = {path: item["get"]["responses"]["200"].get("content", {}).get(said, {}).get("schema")
             for path, item in app.openapi()["paths"].items()
             if path.startswith("/admin") and "get" in item}
    assert {"/admin/health", "/admin/users", "/admin/settings", "/admin/latency"} <= set(reads), "the premise"
    # A handler typed `dict` is written into the contract as an object of anything.
    unsaid = sorted(path for path, schema in reads.items() if (schema or {}).get("type") == "object")
    assert not unsaid, f"these answer an object nobody described: {unsaid}"


def test_each_tab_is_answered_whole_with_one_of_everything_it_can_hold(api):
    """An answer that does not fit what its handler declares is refused by the server itself, a 500 where a
    tab was: so each is read here with one of everything it can hold, and what it holds is looked for. And a
    tab is sent what it draws: the Latency tab was sent every lane's timings, an hourly series and the
    end-to-end span beside its rows, which nothing drew."""
    from libs.db.models import CorpusJob, QueueJob, UserJobState
    from libs.settings import DEFAULTS

    http, db, operator, ordinary, sign_in = api
    sign_in(http, operator)
    found = now() - timedelta(minutes=90)
    read = insert(db, CorpusJob(url=f"https://example.test/{uuid.uuid4()}", source="linkedin", title="Read",
                                posted_at=found - timedelta(minutes=30),
                                fetched_at=found - timedelta(minutes=1), first_seen_at=found,
                                description="x" * 500,
                                description_fetched_at=found + timedelta(minutes=10),
                                described_at=found + timedelta(minutes=10)))
    insert(db, CorpusJob(url=f"https://example.test/{uuid.uuid4()}", source="linkedin", title="Waiting",
                         first_seen_at=now() - timedelta(minutes=5)))
    insert(db, UserJobState(user_id=ordinary.id, job_id=read.id, notified_at=found + timedelta(minutes=20)))
    _dead_job(db, ordinary)
    insert(db, QueueJob(lane="notify", user_id=ordinary.id, payload={}, state="done", created_at=found,
                        claimed_at=found + timedelta(seconds=4), finished_at=found + timedelta(seconds=10)))

    def tab(path: str) -> dict:
        answer = http.get(path)
        assert answer.status_code == 200, f"{path}: {answer.text[:300]}"
        return answer.json()

    health = tab("/admin/health")
    assert [alert["key"] for alert in health["alerts"]] == ["dead_letter:notify"]
    assert {lane["lane"]: lane["dead"] for lane in health["lanes"]}["notify"] == 1

    pipeline = tab("/admin/pipeline")
    assert [stage["key"] for stage in pipeline["stages"]] == ["gather", "describe", "deliver"]
    assert {stage["key"]: stage["waiting"] for stage in pipeline["stages"]}["describe"] == 1
    whose = [(row["email"], row["dead"], row["done"], row["last_error"]) for row in pipeline["lane_accounts"]]
    assert whose == [(ordinary.email, 1, 1, "RuntimeError: Bad Gateway")]
    linkedin = next(row for row in tab("/admin/pipeline/sources")["sources"] if row["source"] == "linkedin")
    assert (linkedin["found"], linkedin["with_text"], linkedin["waiting"]) == (2, 1, 1) and linkedin["newest"]

    latency = tab("/admin/latency")
    assert set(latency) == {"window_hours", "stages"}, "and nothing the tab does not draw"
    rows = {row["key"]: row for row in latency["stages"]}
    assert (rows["found_to_delivered"]["samples"], rows["found_to_delivered"]["p50"]) == (1, 20.0)
    assert rows["posted_to_fetched"]["verdict"] == "unjudged"
    sent = tab("/admin/latency/found_to_delivered")
    assert (sent["lane"]["samples"], sent["lane"]["wait_p50"], sent["lane"]["run_p50"]) == (1, 4.0, 6.0)
    assert [point["samples"] for point in sent["series"]] == [1]
    assert tab("/admin/latency/found_to_described")["backlog"]["waiting"] == 1

    assert set(tab("/admin/settings")["settings"]) == set(DEFAULTS), "every setting, each with what it holds"
    assert all(limit["choices"] for limit in tab("/admin/limits")["limits"])
    assert [job["title"] for job in tab("/admin/corpus")["jobs"]] == ["Waiting", "Read"]
    assert ordinary.email in {row["email"] for row in tab("/admin/users")["users"]}


def test_a_setting_is_stored_only_as_what_it_holds(api):
    """Anything was stored under any setting: text under a number was then read by the harvester as it stood
    (`int("many")`), which stops on it every run, and it is what Admin itself is sent back. Refused, with
    nothing changed; a number written as text is the number."""
    from libs import settings

    http, db, operator, _, sign_in = api
    sign_in(http, operator)
    before = settings.describe_per_cycle(db)

    for key, wrong in (("describe_per_cycle", "many"), ("source_freshness_hours", {"linkedin": "soon"}),
                       ("support_email", 5), ("document_models", ["gpt"])):
        refused = http.put(f"/admin/settings/{key}", json={"value": wrong})
        assert refused.status_code == 400, (key, refused.text)
    assert settings.describe_per_cycle(db) == before
    assert settings.freshness_hours(db, "linkedin") == settings.DEFAULT_SOURCE_FRESHNESS_HOURS["linkedin"]

    kept = http.put("/admin/settings/describe_per_cycle", json={"value": str(before + 5)})
    assert kept.status_code == 200 and kept.json()["settings"]["describe_per_cycle"] == before + 5
    assert settings.describe_per_cycle(db) == before + 5
    assert http.get("/admin/settings").json()["settings"]["describe_per_cycle"] == before + 5
