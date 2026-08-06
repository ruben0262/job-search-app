"""The bell: producing, collapsing, listing, marking read.

The collapse rule carries the weight. Every producer runs on a timer, so a persistent fact would
otherwise be re-announced every cycle — and forty copies of "Telegram is broken" is how a bell gets
ignored and the one real error scrolls away.
"""
from __future__ import annotations

import dataclasses
import os
import uuid

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

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

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(user)
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, session, user
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


def test_a_persistent_fact_collapses_onto_its_unread_row(api):
    """A broken bot re-detected every delivery cycle is one notification, not one per cycle."""
    from libs import notify

    http, session, user = api
    notify.notify(session, user.id, notify.TELEGRAM_BROKEN, "first detection")
    notify.notify(session, user.id, notify.TELEGRAM_BROKEN, "still broken an hour later")
    session.flush()

    body = http.get("/notifications").json()
    assert body["unread"] == 1
    assert len(body["notifications"]) == 1
    assert body["notifications"][0]["body"] == "still broken an hour later"
    assert body["notifications"][0]["level"] == "error"


def test_a_read_notification_allows_a_fresh_unread_one(api):
    """"This is still true" collapses; "this happened again since you looked" does not."""
    from libs import notify

    http, session, user = api
    notify.notify(session, user.id, notify.MODEL_TRAINED, "version 1")
    session.flush()
    first = http.get("/notifications").json()["notifications"][0]
    http.post("/notifications/read", json={"ids": [first["id"]]})

    notify.notify(session, user.id, notify.MODEL_TRAINED, "version 2")
    session.flush()

    body = http.get("/notifications").json()
    assert body["unread"] == 1
    assert len(body["notifications"]) == 2


def test_mark_all_read_covers_what_scrolled_past_the_page(api):
    from libs import notify

    http, session, user = api
    for index in range(3):
        notify.notify(session, user.id,
                      notify.Event(f"test.{index}", "info", f"Event {index}"), "")
    session.flush()

    assert http.get("/notifications").json()["unread"] == 3
    marked = http.post("/notifications/read", json={"all": True}).json()["marked"]
    assert marked == 3
    assert http.get("/notifications").json()["unread"] == 0


def test_the_bell_is_per_user(api):
    """One user's broken bot is not another's notification."""
    from libs import notify
    from libs.db.models import User

    http, session, _user = api
    stranger = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(stranger)
    session.flush()
    notify.notify(session, stranger.id, notify.TELEGRAM_BROKEN, "their problem")
    session.flush()

    assert http.get("/notifications").json()["unread"] == 0


def test_notify_never_raises(api):
    """A notification about a failure must not become a second failure inside the recovery."""
    from libs import notify

    _, session, user = api
    session.close()  # a session in a state nothing should try to use
    notify.notify(session, user.id, notify.TELEGRAM_BROKEN, "should be swallowed")


def test_the_pre_send_refresh_cannot_overrun_the_lanes_claim_timeout():
    """**The failure this prevents stops delivery entirely and reports success.**

    Each refresh is a proxied fetch with retries and backoff. Twelve of them can take minutes, and
    the `notify` lane reclaims a job after two — so the job re-runs, overruns again, and
    `notified_at` is never committed. Every job reads `done`, every account stops receiving
    anything, and nothing errors. That happened within an hour of this refresh first being allowed
    to run.

    The budget has to leave room for rendering and sending inside the same claim.
    """
    from libs.db.models.queue import CLAIM_TIMEOUT_MIN
    from services.notifier.freshness import REFRESH_BUDGET_SECONDS

    claim_seconds = CLAIM_TIMEOUT_MIN["notify"] * 60
    assert claim_seconds / 2 > REFRESH_BUDGET_SECONDS, (
        f"a {REFRESH_BUDGET_SECONDS}s refresh budget against a {claim_seconds}s claim leaves too "
        "little for the send that follows it")


def test_a_slow_refresh_stops_at_the_budget_and_passes_the_rest_through(monkeypatch):
    """Fail open: whatever is not refreshed in time is sent unchanged. A proxy having a bad minute
    must never empty somebody's feed."""
    import time as _time

    from services.notifier import freshness

    clock = {"t": 0.0}
    monkeypatch.setattr(freshness.time, "monotonic", lambda: clock["t"])

    def slow_fetch(url, *, client, stats=None, with_easy_apply=True):
        clock["t"] += freshness.REFRESH_BUDGET_SECONDS  # one fetch eats the whole budget
        return {"applicants": 5, "reposted": None, "easy_apply": None, "workplace": None,
                "posted_at": None, "accepting": True, "applicants_caption": None,
                "application_url": None}

    monkeypatch.setattr(freshness, "fetch_meta", slow_fetch)
    monkeypatch.setattr(freshness, "linkedin_id", lambda url: "123")
    monkeypatch.setattr(freshness, "CorpusRepository", lambda session: _NullCorpus())

    items = [_Item(f"https://linkedin.com/jobs/view/{n}") for n in range(5)]
    out = freshness.date_candidates(object(), items, client=None)

    assert len(out) == len(items), "nothing is dropped when the budget runs out"
    assert {item.url for item in out} == {item.url for item in items}
    assert _time is not None


class _NullCorpus:
    def set_posting_metadata(self, *a, **k): ...
    def mark_closed(self, *a, **k): ...


@dataclasses.dataclass
class _Item:
    """The fields `date_candidates` reads. A dataclass because it calls `replace` on these."""

    url: str
    job_id: int = 1
    posted_at: object = None
    applicants: object = None
    reposted: object = None
    easy_apply: object = None


def test_unlimited_still_fits_inside_one_claim():
    """**Unlimited means "withhold nothing", not "clear the backlog in one transaction".**

    An account with 1,282 waiting and no per-run ceiling built a candidate pool five times that,
    and ranking it did not finish inside the `notify` lane's two-minute claim — so the job was
    reclaimed, re-ran, and the account received nothing while every job reported done.

    The limit's own description already promises the right behaviour: anything over stays queued for
    the next run and nothing is dropped.
    """
    from libs.limits import DEFAULT_SEND_GAP_SECONDS
    from services.notifier.deliver import SEND_BUDGET_SECONDS, unlimited_items
    from services.notifier.eligibility import CANDIDATE_MULTIPLIER

    fetched = unlimited_items(DEFAULT_SEND_GAP_SECONDS)

    # **Derived from the run's own budget rather than picked**, so the two cannot drift: whatever
    # the claim timeout or the pacing becomes, this stays the number a run could actually send.
    assert fetched == int(SEND_BUDGET_SECONDS / DEFAULT_SEND_GAP_SECONDS) + 1
    assert fetched * CANDIDATE_MULTIPLIER <= 1_000, (
        "the candidate pool this implies is too large to rank inside one claim")
    # Never withhold what the run had time for: fetching fewer than it can send is a cap by
    # accident, which is the defect this whole change removes.
    assert fetched > SEND_BUDGET_SECONDS / DEFAULT_SEND_GAP_SECONDS


def test_the_card_says_what_linkedin_said_about_applicants():
    """**The number alone says the opposite of the page.**

    Measured against eight live postings: LinkedIn writes "Be among the first 25 applicants" when
    *fewer than* 25 have applied, and "Over 200 applicants" when it has stopped counting. Both parse
    to a bare integer, and `👥25` tells somebody twenty-five people are ahead of them when the page
    is saying the opposite — the difference between a job worth opening now and one worth skipping.
    """
    from services.notifier.card import applicant_label

    assert applicant_label(25, "Be among the first 25 applicants").startswith("<25")
    assert applicant_label(200, "Over 200 applicants").startswith("200+")
    assert applicant_label(68, "68 applicants").startswith("68")
    # A row stored before the caption was kept still renders from the number alone.
    assert applicant_label(68).startswith("68")
    assert applicant_label(None) == ""


def test_the_feed_item_columns_line_up_with_the_query():
    """`FeedItem(*row[:-1])` maps by position, so a column added to one and not the other shifts
    every field after it — silently, into a card that looks plausible and is wrong."""
    import dataclasses as dc

    from services.notifier.card import FeedItem

    fields = [f.name for f in dc.fields(FeedItem)]
    assert fields.index("applicants_caption") == fields.index("applicants") + 1
    assert fields.index("easy_apply") == fields.index("applicants_caption") + 1
