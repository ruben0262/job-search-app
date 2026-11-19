"""Alerts: named searches a person follows (owner, 2026-11-17), each sending the jobs it catches to the bell,
their own Telegram bot, their browsers and the daily email.

A pass reads the jobs that joined a list since the last one and tests every alert against them
(`libs.alerts.route`); the notifier sends what was caught (`services.notifier.deliver`).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import timedelta

import pytest

from libs.db import get, insert, now
from tests.conftest import needs_db

pytest.importorskip("pywebpush")



@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    """The gap between Telegram messages is Telegram's rule, and the settling moment is the database's; here
    each would only make the suite slow."""
    from libs import alerts
    from services.notifier import deliver

    monkeypatch.setattr(deliver, "sleep", lambda _seconds: None)
    monkeypatch.setattr(alerts, "SETTLE", timedelta(0))


def _alert(db, user, **fields):
    """An alert of theirs, made when they joined unless said otherwise: My search, unless it is given
    something else to look for."""
    from libs.db.models import Alert

    fields.setdefault("search", not any(fields.get(key) for key in ("keywords", "companies", "min_fit")))
    return insert(db, Alert(user_id=user.id, name=fields.pop("name", "My search"),
                            created_at=fields.pop("created_at", user.created_at), **fields))


def _person(db, *, telegram=True, browsers=0, joined_minutes_ago=24 * 60, alert=True, **fields):
    """Somebody with one green word of their own, so no other test's posting can match them, and the My
    search alert every account starts with."""
    from libs.corpus.terms import learn
    from libs.db.models import PushSubscription, User, UserConfig

    word = f"wrangler{uuid.uuid4().hex[:8]}"
    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                           subscription_status=fields.pop("subscription_status", "active"), status="active",
                           active=True, telegram_status="connected" if telegram else "unconfigured",
                           telegram_chat_id="555" if telegram else None,
                           created_at=now() - timedelta(minutes=joined_minutes_ago), **fields))
    insert(db, UserConfig(user_id=user.id, schema_version=2,
                          config={"sections": [{"platform": "all", "keywords": [word]}]}))
    # Saving My search learns its words (`onboarding_routes.save_config`).
    learn(db, [word])
    for _ in range(browsers):
        insert(db, PushSubscription(user_id=user.id, endpoint=f"https://push.example.com/{uuid.uuid4()}",
                                    p256dh="k", auth="a", created_at=user.created_at))
    if alert:
        _alert(db, user)
    return user, word


def _posting(db, title, *, minutes_ago=5, described=True, **fields):
    """A posting found `minutes_ago`, and its whole text read then too unless `described` is False: what a
    board that sends the whole text gives, and what a LinkedIn posting is before it is opened. Its words are
    found as the text is stored (`libs.corpus.words.terms_of`), against the words index."""
    from libs.corpus.words import terms_of
    from libs.db.models import CorpusJob, Word

    seen = now() - timedelta(minutes=minutes_ago)
    description = fields.pop("description", "Build APIs")
    words = terms_of(title, description, sorted(db[Word.COLLECTION].distinct("_id"))) if described else {}
    return insert(db, CorpusJob(url=f"https://jobs.example.com/{uuid.uuid4()}",
                                source=fields.pop("source", "test"),
                                title=title, company=fields.pop("company", "Acme"), description=description,
                                is_remote=True, posted_at=seen, first_seen_at=seen,
                                described_at=seen if described else None,
                                terms_at=seen if described else None,
                                **words, **fields))


def _catch(db, *, back=timedelta(days=5)):
    """One pass of the alerts, from `back` ago for every wait: a test's jobs are minutes or days old, and the
    first pass of a fresh database starts from now. Reading a job again catches nothing twice."""
    from libs.alerts import route
    from libs.db.models import AlertCursor

    for wait in ("0", "72"):
        db[AlertCursor.COLLECTION].update_one({"_id": wait}, {"$set": {"at": now() - back}}, upsert=True)
    return route(db)


def _notified(db, user) -> set[int]:
    from libs.db.models import UserJobState

    return set(db[UserJobState.COLLECTION].distinct(
        "job_id", {"user_id": user.id, "notified_at": {"$ne": None}}))


def _caught(db, user) -> dict[int, list[int]]:
    """Which jobs their alerts caught, and which alerts caught each."""
    from libs.db.models import UserJobState

    return {row["job_id"]: sorted(row["alerts"]) for row in db[UserJobState.COLLECTION].find(
        {"user_id": user.id, "alerted_at": {"$ne": None}})}


def _endpoints(db, user) -> list[str]:
    from libs.db.models import PushSubscription

    return sorted(db[PushSubscription.COLLECTION].distinct("endpoint", {"user_id": user.id}))


def _account(db, user):
    """The account as it is stored now: what a test changed through the API or the notifier."""
    from libs.db.models import User

    return get(db, User, user.id)


class Outbox:
    """Both transports, recording what each was asked to send."""

    def __init__(self, gone=()):
        self.telegram, self.browsers, self.gone = [], [], set(gone)

    def tg(self, chat, text, keyboard):
        self.telegram.append((chat, text, keyboard))

    def push(self, subscription, payload):
        if subscription.endpoint in self.gone:
            return False
        self.browsers.append((subscription.endpoint, payload))
        return True


def run(db, user, outbox, **transports):
    """A pass of the alerts, then this person's notify run."""
    from services.notifier.deliver import deliver

    _catch(db)
    return deliver(db, user.id, telegram=transports.get("telegram", outbox.tg),
                   push=transports.get("push", outbox.push))


# ── what is caught ────────────────────────────────────────────────────────────────────────────

@needs_db
def test_the_alerts_read_new_jobs_from_their_very_first_pass(db, monkeypatch):
    """The first pass starts from now and leaves its place there for the next. It returned before writing it,
    so on a new platform every pass was the first, and no alert ever read a job (live, 2026-11-18): every
    other test here placed the cursor itself, and none ran the alerts from nothing, as a server does."""
    from libs import alerts
    from libs.db.models import AlertCursor

    user, word = _person(db)
    db[AlertCursor.COLLECTION].delete_many({})
    clock = [now() - timedelta(minutes=5)]
    monkeypatch.setattr(alerts, "now", lambda: clock[0])
    assert alerts.route(db).read == 0, "the first pass starts from now"
    assert _caught(db, user) == {}

    job = _posting(db, f"Senior {word}", minutes_ago=2)
    clock[0] = now()
    assert alerts.route(db).read >= 1
    assert list(_caught(db, user)) == [job.id]


@needs_db
def test_a_new_match_reaches_telegram_and_every_browser_once(db):
    from libs.notify import site_url

    user, word = _person(db, browsers=2)
    match = _posting(db, f"Senior {word}")
    _posting(db, "Registered Nurse")
    outbox = Outbox()

    assert run(db, user, outbox) == 1

    [(chat, text, keyboard)] = outbox.telegram
    assert chat == "555"
    assert text.startswith("🔔 My search\n"), "every alert goes to the one chat, headed by its name"
    assert f"Senior {word}" in text and f"Matches: {word}" in text
    # Plain links, so a tap opens something rather than waiting on a webhook.
    page = site_url(f"/#/feed?job={match.id}")
    assert [(b["text"], b["url"]) for b in keyboard["inline_keyboard"][0]] == [
        ("Apply on Test", match.url), ("See it on the site", page)]
    assert [payload["url"] for _, payload in outbox.browsers] == [page, page]
    assert all(payload["body"].startswith("My search · ") for _, payload in outbox.browsers)
    assert _notified(db, user) == {match.id}
    assert run(db, user, Outbox()) == 0, "sent once, and never again"


@needs_db
def test_the_telegram_card_reads_the_salary_off_the_jobs_text(db):
    """The card says "not listed" for a job whose text names the pay unless it reads the text."""
    user, word = _person(db)
    _posting(db, f"Senior {word}", description="Build APIs. Pays €60,000 - €80,000 per year.")
    outbox = Outbox()

    assert run(db, user, outbox) == 1
    [(_chat, text, _keyboard)] = outbox.telegram
    assert "💰 not listed" not in text and "60" in text.split("💰", 1)[1].split("\n", 1)[0]


@needs_db
def test_a_job_is_caught_once_it_has_its_text_not_when_it_is_found(db):
    """A job without its description is not a whole job yet (owner, 2026-11-02), and it joins a list when its
    text is read: one opened after a newer one was sent is still caught, or a LinkedIn posting found early and
    opened late would fall behind for good."""
    from libs.corpus.repository import CorpusRepository

    user, word = _person(db)
    early = _posting(db, f"Senior {word}", minutes_ago=30, described=False)
    later = _posting(db, word, minutes_ago=10)

    run(db, user, Outbox())
    assert _notified(db, user) == {later.id}, "not the one with no text yet"

    CorpusRepository(db).set_description(early.id, f"The whole {word} posting. " * 20)
    run(db, user, Outbox())
    assert _notified(db, user) == {later.id, early.id}


@needs_db
def test_an_alert_announces_what_joins_the_list_after_it_was_made(db):
    """Not the backlog the Jobs page already shows: a job read before the alert existed is not sent."""
    user, word = _person(db, alert=False)
    _posting(db, word, minutes_ago=60)
    _alert(db, user, created_at=now() - timedelta(minutes=30))
    new = _posting(db, word, minutes_ago=10)

    run(db, user, Outbox())

    assert _notified(db, user) == {new.id}


@needs_db
def test_a_company_alert_catches_that_employer_by_its_name_and_nothing_else(db):
    """Follow a company (owner, 2026-11-17): whole words of its name, in any case, whatever its search."""
    user, _ = _person(db, alert=False)
    lego = _alert(db, user, name="LEGO", companies=["LEGO"])
    theirs = _posting(db, "Brick Designer", company="The LEGO Group")
    _posting(db, "Ride Operator", company="Legoland Billund")
    _posting(db, "Designer", company="Acme")

    _catch(db)

    assert _caught(db, user) == {theirs.id: [lego.id]}


@needs_db
def test_places_are_countries_or_cities_and_job_sites_narrow_them(db):
    """A place is a country or a city; any of them will do. Every field given must hold."""
    user, word = _person(db, alert=False)
    alert = _alert(db, user, name="Here", keywords=[word], places=["denmark", "berlin"],
                   sources=["greenhouse"])
    copenhagen = _posting(db, word, country="DK", source="greenhouse")
    berlin = _posting(db, word, country="DE", city="berlin", source="greenhouse")
    _posting(db, word, country="DE", city="munich", source="greenhouse")
    _posting(db, word, country="DK", source="linkedin")
    _posting(db, "Nurse", country="DK", source="greenhouse")

    _catch(db)

    assert _caught(db, user) == {copenhagen.id: [alert.id], berlin.id: [alert.id]}


@needs_db
def test_a_word_is_found_in_the_text_as_well_as_the_title(db):
    """Words are matched as My search's are: anywhere in the posting, whole words."""
    user, word = _person(db, alert=False)
    alert = _alert(db, user, name="Words", keywords=[word])
    in_text = _posting(db, "Engineer", description=f"You will run {word} every day.")
    _posting(db, "Engineer", description=f"You will run {word}x every day.")

    _catch(db)

    assert _caught(db, user) == {in_text.id: [alert.id]}


@needs_db
def test_a_posting_stored_without_its_words_is_still_caught_by_them(db):
    """Its words are found in the pass, as they would have been with its text, not passed over."""
    from libs.db.models import CorpusJob

    user, word = _person(db, alert=False)
    alert = _alert(db, user, name="Words", keywords=[word])
    job = _posting(db, word)
    db[CorpusJob.COLLECTION].update_one({"_id": job.id}, {"$set": {"terms": [], "terms_at": None}})

    _catch(db)

    assert _caught(db, user) == {job.id: [alert.id]}


@needs_db
def test_my_search_holds_to_the_jobs_list_and_another_alert_does_not(db):
    """"My search" sends what the Jobs page lists, red words and all; an alert of its own is its own
    search."""
    from libs.db.models import UserConfig

    user, word = _person(db)
    db[UserConfig.COLLECTION].update_one({"user_id": user.id}, {"$set": {"config": {"sections": [
        {"keywords": [word], "exclude_keywords": ["senior"]}]}}})
    words = _alert(db, user, name="Anything", keywords=[word])
    [mine] = [alert for alert in _alerts(db, user) if alert.search]
    junior, senior = _posting(db, f"Junior {word}"), _posting(db, f"Senior {word}")

    _catch(db)

    assert _caught(db, user) == {junior.id: sorted([mine.id, words.id]), senior.id: [words.id]}


@needs_db
def test_my_search_catches_a_word_the_index_does_not_have_yet_and_learns_it(db):
    """A word saved some other way than My search's own page is not looked for in the postings yet; the Jobs
    list still has their jobs, so the alert must too, and the next posting is searched for it."""
    from libs.db.models import UserConfig, Word

    user, _ = _person(db)
    word = f"unindexed{uuid.uuid4().hex[:6]}"
    db[UserConfig.COLLECTION].update_one({"user_id": user.id},
                                         {"$set": {"config": {"sections": [{"keywords": [word]}]}}})
    job = _posting(db, f"Senior {word}")

    _catch(db)

    assert list(_caught(db, user)) == [job.id]
    assert db[Word.COLLECTION].count_documents({"_id": word}) == 1


@needs_db
def test_on_lists_says_what_each_persons_jobs_page_lists(db):
    """The one question asked twice: the alerts ask it of everybody at once (`on_lists`), the Jobs page one
    person at a time (`their_jobs`). Red words, places, a minimum salary, a skipped job and a plan's wait all
    decide; the two answers must be the same for everybody."""
    from libs.db.models import User, UserConfig
    from libs.db.models.tenant import record_state
    from libs.matching import on_lists, their_jobs

    word = f"wrangler{uuid.uuid4().hex[:8]}"
    configs = [{"sections": [{"keywords": [word]}]},
               {"sections": [{"keywords": [word], "exclude_keywords": ["senior"]}]},
               {"sections": [{"keywords": [word], "locations": ["denmark"]}]},
               {"sections": [{"keywords": [word]}], "min_salary": 90000, "salary_currency": "EUR"}]
    people = []
    for index, config in enumerate([*configs, configs[0]]):
        user, _ = _person(db, alert=False, **({"subscription_plan": "starter"} if index == 4 else {}))
        db[UserConfig.COLLECTION].update_one({"user_id": user.id}, {"$set": {"config": config}})
        people.append(get(db, User, user.id))
    jobs = [_posting(db, f"Senior {word}", country="DK"), _posting(db, f"Junior {word}", country="DE"),
            _posting(db, word, description="Build APIs. Pays €40,000 - €50,000 per year.", country="DK"),
            _posting(db, word, minutes_ago=4 * 24 * 60, country="DE"), _posting(db, "Nurse")]
    record_state(db, people[0].id, jobs[1].id, dismissed_at=now())
    ids = [job.id for job in jobs]

    batched = on_lists(db, {person.id: ids for person in people})

    for person in people:
        listed = {match.job.id for match in their_jobs(db, person)} & set(ids)
        assert batched.get(person.id, set()) == listed, person.email
    assert any(batched.values()), "the premise: some of these are on somebody's list"


@needs_db
def test_a_match_score_needs_the_documents_it_is_made_from(db, monkeypatch):
    """A minimum score is read from the person's documents (`libs.fit`): without them it catches nothing, and
    with them a job at or above it is caught."""
    from libs import fit
    from libs.db.models import CorpusJob

    user, word = _person(db, alert=False)
    alert = _alert(db, user, name="Good fits", keywords=[word], min_fit=1)
    job = _posting(db, f"Python {word} developer", description="Build Python APIs. " * 30)
    db[CorpusJob.COLLECTION].update_one({"_id": job.id}, {"$set": {
        "fit": fit.job(job.title, job.description, "en"), "fit_version": fit.VERSION}})

    monkeypatch.setattr(fit, "profile", lambda _db, _user: None)
    _catch(db)
    assert _caught(db, user) == {}, "no documents, no score, nothing caught"

    made = fit.person({"cv": ["Python developer building APIs."]})
    monkeypatch.setattr(fit, "profile", lambda _db, _user: made)
    _catch(db)
    assert _caught(db, user) == {job.id: [alert.id]}


@needs_db
def test_starter_hears_of_a_job_when_its_own_list_has_it(db):
    """Owner, 2026-11-17: Starter's alerts keep its three days. A job reaches an alert when it joins the
    person's list: at once for a member, three days later for Starter."""
    member, word = _person(db, joined_minutes_ago=10 * 24 * 60)
    starter, _ = _person(db, joined_minutes_ago=10 * 24 * 60, subscription_plan="starter")
    from libs.db.models import UserConfig
    db[UserConfig.COLLECTION].update_one({"user_id": starter.id}, {"$set": {"config": {"sections": [
        {"keywords": [word]}]}}})
    today, four_days = _posting(db, word, minutes_ago=60), _posting(db, word, minutes_ago=4 * 24 * 60)

    _catch(db)

    assert (set(_caught(db, starter)), set(_caught(db, member))) == ({four_days.id}, {today.id, four_days.id})


@needs_db
def test_nobody_without_alerts_or_with_them_switched_off_has_anything_caught(db):
    """A free account has no alerts (`libs.membership`); a switched-off one sends nothing, its switch being
    the only on and off (owner, 2026-11-19)."""
    from libs.db.models import Alert, UserConfig

    _free, word = _person(db, subscription_status=None)
    off, _ = _person(db)
    db[UserConfig.COLLECTION].update_one({"user_id": off.id}, {"$set": {"config": {"sections": [
        {"keywords": [word]}]}}})
    db[Alert.COLLECTION].update_many({"user_id": off.id}, {"$set": {"on": False}})
    _posting(db, word)

    assert _catch(db).caught == 0


@needs_db
def test_an_account_that_had_paused_its_alerts_has_every_one_switched_off_once(db):
    """The pause went (owner, 2026-11-19), and each alert's switch is the only one: somebody who had paused
    keeps hearing nothing, an account not yet confirmed (never "active" either) is not taken for paused, and
    it is done once ever, so a switch turned back on afterwards stays on."""
    from libs.alerts import paused_for_existing
    from libs.db.models import Alert, User

    paused, _ = _person(db)
    unconfirmed, _ = _person(db)
    hearing, _ = _person(db)
    db[User.COLLECTION].update_many({"_id": {"$in": [paused.id, unconfirmed.id]}},
                                    {"$set": {"active": False}})
    db[User.COLLECTION].update_one({"_id": unconfirmed.id}, {"$set": {"status": "pending_verification"}})
    db.counters.delete_one({"_id": "alerts.paused_for_existing"})

    def on(user):
        return [alert["on"] for alert in db[Alert.COLLECTION].find({"user_id": user.id})]

    assert paused_for_existing(db) >= 1
    assert (on(paused), on(unconfirmed), on(hearing)) == ([False], [True], [True])

    db[Alert.COLLECTION].update_many({"user_id": paused.id}, {"$set": {"on": True}})
    assert paused_for_existing(db) == 0
    assert on(paused) == [True]


@needs_db
def test_a_backlog_is_read_a_batch_a_pass_and_nothing_is_lost(db, monkeypatch):
    """A notifier back from a stop reads what joined the lists meanwhile `PASS_JOBS` at a time, oldest first,
    each pass from where the last one got to."""
    from libs import alerts

    monkeypatch.setattr(alerts, "PASS_JOBS", 2)
    user, word = _person(db)
    jobs = [_posting(db, word, minutes_ago=minutes) for minutes in (30, 20, 10)]

    _catch(db)
    assert set(_caught(db, user)) == {jobs[0].id}, "the oldest; the last moment read is the next pass's"
    alerts.route(db)
    alerts.route(db)
    assert set(_caught(db, user)) == {job.id for job in jobs}


@needs_db
def test_the_first_pass_starts_from_now(db):
    """A fresh notifier announces what is new, not every job the Jobs page already shows."""
    from libs.alerts import route

    user, word = _person(db)
    _posting(db, word)

    assert route(db).caught == 0
    assert _caught(db, user) == {}


# ── where it is sent ──────────────────────────────────────────────────────────────────────────

@needs_db
def test_a_job_two_alerts_caught_is_sent_once_headed_by_both(db):
    user, word = _person(db, alert=False)
    _alert(db, user, name="Python jobs", keywords=[word])
    _alert(db, user, name="Acme", companies=["Acme"])
    job = _posting(db, word)
    outbox = Outbox()

    assert run(db, user, outbox) == 1

    [(_chat, text, _keyboard)] = outbox.telegram
    assert text.startswith("🔔 Python jobs, Acme\n")
    assert _notified(db, user) == {job.id}


@needs_db
def test_each_alert_sends_only_where_it_was_told(db):
    """An alert's channels are its own: the bell alone, or Telegram alone."""
    from libs.db.models import UserNotification

    user, word = _person(db, browsers=1, alert=False)
    quiet = _alert(db, user, name="Quiet", keywords=[word], channels=["bell"])
    _posting(db, word)
    outbox = Outbox()

    assert run(db, user, outbox) == 1
    assert outbox.telegram == [] and outbox.browsers == []
    [bell] = db[UserNotification.COLLECTION].find({"user_id": user.id})
    assert (bell["event"], bell["title"], bell["url"]) == (
        f"alert.{quiet.id}", "Quiet", f"#/alerts?alert={quiet.id}")

    loud = _alert(db, user, name="Loud", companies=["Acme"], channels=["telegram"])
    _posting(db, "Engineer")
    assert run(db, user, outbox) == 1
    assert [text.split("\n", 1)[0] for _, text, _ in outbox.telegram] == ["🔔 Loud"]
    assert db[UserNotification.COLLECTION].count_documents({"event": f"alert.{loud.id}"}) == 0


@needs_db
def test_the_bell_keeps_one_line_an_alert_not_one_a_job(db):
    from libs.db.models import UserNotification

    user, word = _person(db, telegram=False)
    for minutes in (30, 20, 10):
        _posting(db, f"Job {minutes} {word}", minutes_ago=minutes)

    assert run(db, user, Outbox()) == 3
    [bell] = db[UserNotification.COLLECTION].find({"user_id": user.id})
    assert bell["body"].startswith(f"Job 10 {word}"), "brought up to date with the newest"


@needs_db
def test_a_deleted_alert_sends_nothing_more(db):
    from libs.db.models import Alert

    user, word = _person(db)
    _posting(db, word)
    _catch(db)
    db[Alert.COLLECTION].delete_many({"user_id": user.id})
    outbox = Outbox()

    from services.notifier.deliver import deliver
    deliver(db, user.id, telegram=outbox.tg, push=outbox.push)

    assert outbox.telegram == [] and _notified(db, user) != set(), "handled, and nothing sent"


@needs_db
def test_connecting_telegram_later_does_not_send_what_was_already_caught(db):
    """Turning Telegram on weeks after joining must not fire every job already caught at once: the bell had
    those."""
    from libs.db.models import User

    user, word = _person(db, telegram=False)
    _posting(db, word, minutes_ago=60)
    run(db, user, Outbox())
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
        "telegram_status": "connected", "telegram_chat_id": "555", "telegram_connected_at": now()}})
    new = _posting(db, word, minutes_ago=1)
    outbox = Outbox()

    run(db, user, outbox)

    assert [text for _, text, _ in outbox.telegram if str(new.title) in text] and len(outbox.telegram) == 1


@needs_db
def test_the_daily_cap_holds_the_rest_for_tomorrow(db):
    from libs import limits
    from libs.notify import owed

    user, word = _person(db)
    limits.set_limit(db, user.id, "notifications_per_day", 1)
    first = _posting(db, word, minutes_ago=20)
    second = _posting(db, word, minutes_ago=10)

    assert run(db, user, Outbox()) == 1
    assert _notified(db, user) == {first.id}, "oldest first"
    assert run(db, user, Outbox()) == 0
    assert [row["job_id"] for row in owed(db, user.id)] == [second.id], "held, not dropped"


@needs_db
def test_with_no_cap_a_run_sends_everything_owed(db):
    user, word = _person(db)
    for index in range(14):
        _posting(db, word, minutes_ago=30 - index)

    assert run(db, user, Outbox()) == 14


# ── when a channel fails ──────────────────────────────────────────────────────────────────────

@needs_db
def test_a_browser_that_has_gone_is_deleted_and_the_others_still_get_the_job(db):
    user, word = _person(db, telegram=False, browsers=2)
    job = _posting(db, word)
    gone, kept = _endpoints(db, user)
    outbox = Outbox(gone={gone})

    assert run(db, user, outbox) == 1
    assert [endpoint for endpoint, _ in outbox.browsers] == [kept]
    assert _endpoints(db, user) == [kept]
    assert _notified(db, user) == {job.id}


@needs_db
def test_a_blocked_bot_marks_telegram_broken_and_the_browser_still_gets_the_job(db):
    from libs.db.models import UserNotification

    user, word = _person(db, browsers=1)
    job = _posting(db, word)
    outbox = Outbox()

    def blocked(chat, text, keyboard):
        raise RuntimeError("Forbidden: bot was blocked by the user")

    assert run(db, user, outbox, telegram=blocked) == 1
    assert _account(db, user).telegram_status == "broken"
    assert "telegram.broken" in db[UserNotification.COLLECTION].distinct("event", {"user_id": user.id})
    assert len(outbox.browsers) == 1
    assert _notified(db, user) == {job.id}


@needs_db
def test_a_telegram_outage_is_retried_rather_than_disconnecting(db):
    """A bad minute at Telegram must not cost somebody their connection."""
    user, word = _person(db)
    _posting(db, word)

    def down(chat, text, keyboard):
        raise RuntimeError("Telegram answered 3 server error(s) in a row")

    with pytest.raises(RuntimeError):
        run(db, user, Outbox(), telegram=down)
    assert _account(db, user).telegram_status == "connected"
    assert _notified(db, user) == set()


@needs_db
def test_a_run_that_fails_partway_sends_nothing_twice(db):
    user, word = _person(db)
    jobs = [_posting(db, word, minutes_ago=50 - index) for index in range(5)]
    outbox = Outbox()

    def fourth_fails(chat, text, keyboard):
        if len(outbox.telegram) == 3:
            raise RuntimeError("Bad Gateway")
        outbox.tg(chat, text, keyboard)

    with pytest.raises(RuntimeError):
        run(db, user, outbox, telegram=fourth_fails)
    # Each send was recorded the moment it went, so the failure keeps the three that did.
    assert _notified(db, user) == {job.id for job in jobs[:3]}

    assert run(db, user, Outbox()) == 2, "only the two that never went"
    assert _notified(db, user) == {job.id for job in jobs}


@needs_db
def test_a_run_stops_before_its_claim_expires_and_queues_the_rest(db, monkeypatch):
    import time

    from libs import limits
    from libs.db.models import QueueJob
    from libs.notify import owed
    from services.notifier import deliver as deliver_module

    user, word = _person(db)
    limits.set_limit(db, user.id, "items_per_run", 50)
    for index in range(10):
        _posting(db, word, minutes_ago=30 - index)
    monkeypatch.setattr(deliver_module, "SEND_BUDGET_SECONDS", 0.05)
    outbox = Outbox()

    def slow(chat, text, keyboard):
        outbox.tg(chat, text, keyboard)
        time.sleep(0.04)

    sent = run(db, user, outbox, telegram=slow)

    assert 0 < sent < 10
    assert len(owed(db, user.id)) == 10 - sent, "what did not go is still owed, not lost"
    assert db[QueueJob.COLLECTION].count_documents(
        {"lane": "notify", "user_id": user.id, "state": "ready"}) == 1


@needs_db
def test_telegram_with_no_bot_token_stored_still_reaches_browsers_and_says_so(db, caplog):
    user, word = _person(db, browsers=1)
    _posting(db, word)
    outbox = Outbox()

    with caplog.at_level(logging.WARNING):
        assert run(db, user, outbox, telegram=None) == 1

    assert outbox.telegram == [] and len(outbox.browsers) == 1
    assert any("no bot token stored" in record.getMessage() for record in caplog.records)


def test_the_push_transport_reads_404_and_410_as_gone(monkeypatch):
    """The only answer that deletes a subscription. Anything else is a fault and is raised."""
    from types import SimpleNamespace

    from pywebpush import WebPushException

    from libs.notify import push

    browser = SimpleNamespace(endpoint="https://push.example.com/x", p256dh="k", auth="a")
    alert = {"title": "Backend Engineer", "body": "Acme", "url": "https://jobs.example.com/#/feed?job=1"}
    seen = {}

    def answering(status):
        def webpush(subscription_info, data, **_kwargs):
            seen.update(keys=subscription_info["keys"], alert=json.loads(data))
            if status >= 400:
                raise WebPushException("push failed", response=SimpleNamespace(status_code=status))
        return webpush

    monkeypatch.setattr(push, "webpush", answering(201))
    assert push.send(browser, alert, "key") is True
    assert seen == {"keys": {"p256dh": "k", "auth": "a"}, "alert": alert}
    for status in (404, 410):
        monkeypatch.setattr(push, "webpush", answering(status))
        assert push.send(browser, alert, "key") is False
    monkeypatch.setattr(push, "webpush", answering(500))
    with pytest.raises(WebPushException):
        push.send(browser, alert, "key")


def _alerts(db, user):
    from libs.db import find
    from libs.db.models import Alert

    return find(db, Alert, {"user_id": user.id})


# ── connecting their own bot, through the API ─────────────────────────────────────────────────

class FakeTelegram:
    """Telegram's Bot API for bots made in these tests: tokens as BotFather issues them, a /start when
    the test presses Start, and every message a bot was asked to send."""

    def __init__(self):
        self.bots: dict[str, str] = {}
        self.updates: dict[str, list] = {}
        self.sent: list[tuple[str, str, str]] = []
        self.calls = 0

    def make_bot(self, name="alice_jobs_bot") -> str:
        token = f"{7000000000 + len(self.bots)}:AAH{uuid.uuid4().hex}"
        self.bots[token], self.updates[token] = name, []
        return token

    def press_start(self, token, chat=4242):
        self.updates[token].append({"update_id": len(self.updates[token]) + 1, "message": {
            "message_id": 1, "text": "/start", "chat": {"id": chat, "type": "private"}}})

    def post(self, url, json=None, timeout=None):
        import httpx

        self.calls += 1
        token, method = url.split("/bot", 1)[1].split("/", 1)
        if token not in self.bots:
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        result = {"getMe": lambda: {"username": self.bots[token]},
                  "getUpdates": lambda: self.updates[token],
                  "sendMessage": lambda: self.sent.append((token, str(json["chat_id"]), json["text"])) or {},
                  }[method]()
        return httpx.Response(200, json={"ok": True, "result": result})


@pytest.fixture()
def telegram(monkeypatch):
    from libs import telegram as bot_api

    fake = FakeTelegram()
    monkeypatch.setattr(bot_api.httpx, "post", fake.post)
    return fake


@pytest.fixture()
def api(monkeypatch, db):
    from libs.secrets import generate_key

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("SECRETS_KEK", f"k1:{generate_key()}")
    monkeypatch.setenv("PUBLIC_URL", "https://jobs.example.com")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db.models import User
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                           subscription_status="active", status="active"))
    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user


def _connect(http, telegram) -> str:
    token = telegram.make_bot()
    http.put("/telegram/bot", json={"token": token})
    telegram.press_start(token)
    assert http.post("/telegram/check").json()["status"] == "connected"
    return token


@needs_db
def test_telegram_connects_with_the_bots_token_and_one_press_of_start(api, telegram):
    """No chat id to look up: pressing Start in their bot is how we learn it."""
    http, db, user = api
    token = telegram.make_bot()

    saved = http.put("/telegram/bot", json={"token": token}).json()
    assert saved == {"status": "waiting", "bot": "alice_jobs_bot", "open_url": "https://t.me/alice_jobs_bot"}
    assert http.post("/telegram/check").json()["status"] == "waiting", "Start not pressed yet"

    telegram.press_start(token, chat=4242)
    assert http.post("/telegram/check").json()["status"] == "connected"

    account = _account(db, user)
    assert (account.telegram_status, account.telegram_chat_id) == ("connected", "4242")
    [(bot, chat, text)] = telegram.sent
    assert (bot, chat) == (token, "4242") and "Connected" in text, "a welcome, through their own bot"
    assert http.get("/ai-accounts").json()["accounts"] == [], "a bot token is not one of the AI accounts"


@needs_db
def test_something_that_is_not_a_bot_token_is_refused_without_asking_telegram(api, telegram):
    http, db, user = api

    refused = http.put("/telegram/bot", json={"token": "my telegram is @alice, thanks!"})

    assert refused.status_code == 422 and "does not look like a bot token" in refused.json()["detail"]
    assert telegram.calls == 0
    assert _account(db, user).telegram_status == "unconfigured"


@needs_db
def test_a_token_telegram_does_not_know_is_refused_and_not_kept(api, telegram):
    from libs.db.models import UserSecret

    http, db, user = api

    refused = http.put("/telegram/bot", json={"token": f"7999999999:AAH{uuid.uuid4().hex}"})

    assert refused.status_code == 422 and "did not accept" in refused.json()["detail"]
    assert db[UserSecret.COLLECTION].count_documents({"user_id": user.id}) == 0


@needs_db
def test_a_test_message_goes_through_their_own_bot(api, telegram):
    http, _, _ = api
    token = _connect(http, telegram)

    assert http.post("/telegram/test").json()["status"] == "connected"

    bot, chat, text = telegram.sent[-1]
    assert (bot, chat) == (token, "4242") and "test" in text


@needs_db
def test_disconnecting_forgets_the_bot_and_its_chat(api, telegram):
    from libs.db.models import UserSecret

    http, db, user = api
    _connect(http, telegram)

    assert http.delete("/telegram/bot").json()["status"] == "unconfigured"

    account = _account(db, user)
    assert (account.telegram_status, account.telegram_chat_id, account.telegram_bot_name) == (
        "unconfigured", None, None)
    assert db[UserSecret.COLLECTION].count_documents({"user_id": user.id}) == 0


@needs_db
def test_the_ai_accounts_cannot_store_a_bot_token(api, telegram):
    """One way in for a bot token: the Telegram steps, which also find the chat."""
    http, _, _ = api
    assert http.post("/ai-accounts/keys", json={"kind": "telegram_bot", "value": telegram.make_bot(),
                                                "allow_unvalidated": True}).status_code == 422


@needs_db
def test_the_notifier_sends_each_person_their_jobs_through_their_own_bot(api, telegram):
    """Two people, two bots: each one's jobs leave through the token that person saved."""
    from libs.db import insert
    from libs.db.models import QueueJob, User, UserConfig
    from libs.secrets import SecretStore
    from services.notifier.__main__ import handle

    http, db, user = api
    alice_bot = _connect(http, telegram)
    word = f"wrangler{uuid.uuid4().hex[:8]}"
    insert(db, UserConfig(user_id=user.id, schema_version=2,
                          config={"sections": [{"platform": "all", "keywords": [word]}]}))
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
        "active": True, "created_at": now() - timedelta(days=1),
        "telegram_connected_at": now() - timedelta(hours=1)}})
    _alert(db, get(db, User, user.id))
    bob, _ = _person(db)
    bob_bot = telegram.make_bot("bob_jobs_bot")
    SecretStore(db).put(bob.id, "telegram_bot", bob_bot, validated=True)
    job = _posting(db, f"Senior {word}")
    _catch(db)

    handle(db, insert(db, QueueJob(lane="notify", user_id=user.id)))
    handle(db, insert(db, QueueJob(lane="notify", user_id=bob.id)))

    alerts = [(bot, chat) for bot, chat, text in telegram.sent if job.title in text]
    assert alerts == [(alice_bot, "4242")], "Alice's job, through Alice's bot, and nothing through Bob's"


@needs_db
def test_notifications_turn_on_with_nothing_set_up_and_an_alert_reaches_the_browser_signed_by_its_key(
        api, monkeypatch):
    """The live site ran a night with no key pair set, so every "Turn on notifications" ended in "not set up"
    and no phone ever heard of a job (2026-11-18). The pair is now the platform's own, made on first asking:
    the key a browser subscribes with is the one its alerts are signed with, and they open with its keys."""
    import base64
    from types import SimpleNamespace

    import http_ece
    import requests
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    from libs.db.models import QueueJob, User, UserConfig
    from services.notifier.__main__ import handle

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def unb64(text):
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

    http, db, user = api
    served = http.get("/push/key")
    assert served.status_code == 200, served.text
    key = served.json()["key"]
    assert http.get("/push/key").json() == {"key": key}, "one pair, kept"

    # A browser as Chrome's is: its own P-256 pair and auth secret, subscribed through the site.
    phone, auth = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
    endpoint = f"https://push.example.com/{uuid.uuid4()}"
    point = phone.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
    assert http.post("/push/subscribe", json={"endpoint": endpoint, "keys": {
        "p256dh": b64(point), "auth": b64(auth)}}).status_code == 200

    word = f"wrangler{uuid.uuid4().hex[:8]}"
    insert(db, UserConfig(user_id=user.id, schema_version=2,
                          config={"sections": [{"platform": "all", "keywords": [word]}]}))
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"active": True,
                                                              "created_at": now() - timedelta(days=1)}})
    _alert(db, get(db, User, user.id))
    job = _posting(db, f"Senior {word}")
    _catch(db)
    posted = []
    monkeypatch.setattr(requests, "post", lambda url, **sent: posted.append((url, sent)) or SimpleNamespace(
        status_code=201, text="", reason="Created", headers={}))
    handle(db, insert(db, QueueJob(lane="notify", user_id=user.id)))

    assert [url for url, _ in posted] == [endpoint]
    sent = posted[0][1]
    token, signer = sent["headers"]["Authorization"].removeprefix("vapid t=").split(",k=")
    assert signer == key, "signed by the key the browser subscribed with"
    head, claims, signature = token.split(".")
    numbers = unb64(signature)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(key)).verify(
        encode_dss_signature(int.from_bytes(numbers[:32]), int.from_bytes(numbers[32:])),
        f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert json.loads(unb64(claims))["sub"] == "https://jobs.example.com"
    alert = json.loads(http_ece.decrypt(sent["data"], private_key=phone, auth_secret=auth))
    assert alert["title"] == job.title


@needs_db
def test_a_test_notification_reaches_this_browser_and_one_its_service_dropped_is_forgotten(api, monkeypatch):
    """"Send a test", as Telegram has: a way to see a notification arrive on a phone without waiting for a
    job (owner, 2026-11-18: "make sure the notifications can somehow come through"). It goes to this browser
    alone, signed as an alert is; a refusal says what the push service answered, and a browser it says is gone
    is forgotten and said to be."""
    from types import SimpleNamespace

    from pywebpush import WebPushException

    from libs.notify import push

    http, db, user = api
    phone, laptop = ({"endpoint": f"https://push.example.com/{uuid.uuid4()}",
                      "keys": {"p256dh": "BKey", "auth": "secret"}} for _ in range(2))
    for browser in (phone, laptop):
        assert http.post("/push/subscribe", json=browser).status_code == 200
    sent, answer = [], [201]

    def webpush(subscription_info, data, **options):
        sent.append((subscription_info["endpoint"], json.loads(data)["title"], options["vapid_private_key"]))
        if answer[0] >= 400:
            raise WebPushException("refused", response=SimpleNamespace(status_code=answer[0], text=""))

    monkeypatch.setattr(push, "webpush", webpush)
    tried = {"endpoint": phone["endpoint"]}
    assert http.post("/push/test", json=tried).json() == {"status": "sent"}
    assert sent == [(phone["endpoint"], "Notifications are on", push.private_key(db))]

    answer[0] = 403
    refused = http.post("/push/test", json=tried)
    assert refused.status_code == 502 and "said no (403)" in refused.json()["detail"]
    answer[0] = 410
    assert http.post("/push/test", json=tried).status_code == 410
    assert _endpoints(db, user) == [laptop["endpoint"]]
    assert http.post("/push/test", json=tried).status_code == 409, "forgotten, so not signed up"


@needs_db
def test_the_alerts_page_says_where_its_alerts_can_reach(api):
    """Where an alert sends is set up inside it (owner, 2026-11-19), and a card says when a place it sends to
    is not: so the page is told their Telegram and how many phones or browsers have notifications on."""
    http, _, _ = api
    page = http.get("/alerts").json()
    assert (page["telegram"], page["phones"]) == ("unconfigured", 0)
    phone = {"endpoint": f"https://push.example.com/{uuid.uuid4()}",
             "keys": {"p256dh": "BKey", "auth": "secret"}}
    assert http.post("/push/subscribe", json=phone).status_code == 200
    assert http.get("/alerts").json()["phones"] == 1


@needs_db
def test_a_browser_subscribes_once(api):
    from libs.db.models import PushSubscription

    http, db, user = api
    browser = {"endpoint": f"https://push.example.com/{uuid.uuid4()}", "expirationTime": None,
               "keys": {"p256dh": "BKey", "auth": "secret"}}

    # The same browser twice is one subscription.
    assert http.post("/push/subscribe", json=browser).status_code == 200
    assert http.post("/push/subscribe", json=browser).status_code == 200
    assert _endpoints(db, user) == [browser["endpoint"]]
    assert db[PushSubscription.COLLECTION].count_documents({"endpoint": browser["endpoint"]}) == 1


@needs_db
def test_a_push_endpoint_has_to_be_https(api):
    """The notifier posts to it, so an address inside the network is refused at the door."""
    http, _, _ = api
    response = http.post("/push/subscribe", json={"endpoint": "http://169.254.169.254/latest",
                                                  "keys": {"p256dh": "k", "auth": "a"}})
    assert response.status_code == 422


# ── the Alerts page, through the API ──────────────────────────────────────────────────────────

LEGO = {"name": "LEGO jobs", "companies": ["LEGO"], "places": ["denmark", "berlin"], "sources": ["linkedin"],
        "channels": ["bell", "telegram"]}


@needs_db
def test_an_alert_is_made_listed_changed_and_deleted(api):
    http, _, _ = api

    made = http.post("/alerts", json={**LEGO, "keywords": ["  Brick Designer ", "brick designer"]})
    assert made.status_code == 200, made.text
    alert = made.json()
    assert (alert["name"], alert["keywords"], alert["companies"], alert["on"]) == (
        "LEGO jobs", ["brick designer"], ["LEGO"], True), "words as words are compared, each once"

    page = http.get("/alerts").json()
    assert [each["name"] for each in page["alerts"]] == ["LEGO jobs"]
    assert page["can"] is True and page["most"] == 10
    assert {"value": "berlin", "label": "Berlin"} in page["places"]
    assert {"value": "linkedin", "label": "LinkedIn"} in page["sources"]

    off = http.put(f"/alerts/{alert['id']}", json={**LEGO, "on": False}).json()
    assert off["on"] is False and off["keywords"] == []
    assert http.delete(f"/alerts/{alert['id']}").status_code == 200
    assert http.get("/alerts").json()["alerts"] == []


@needs_db
def test_an_alerts_words_are_looked_for_from_the_next_posting_on(api):
    """Saving an alert learns its words, so the very next posting is searched for them
    (`libs.corpus.terms`)."""
    from libs.db.models import Word

    http, db, _ = api
    http.post("/alerts", json={"name": "Rust", "keywords": ["Rust Engineer"]})

    assert db[Word.COLLECTION].count_documents({"_id": "rust engineer"}) == 1


@needs_db
def test_an_alert_must_look_for_something_somewhere_it_knows(api):
    """A place or a job site alone is every job there; a place the page does not offer is a mistake."""
    http, _, _ = api

    nothing = http.post("/alerts", json={"name": "Denmark", "places": ["denmark"]})
    assert nothing.status_code == 422 and "knows what to look for" in nothing.json()["detail"]
    assert http.post("/alerts", json={**LEGO, "places": ["atlantis"]}).status_code == 422
    assert http.post("/alerts", json={**LEGO, "sources": ["myspace"]}).status_code == 422
    assert http.post("/alerts", json={**LEGO, "channels": ["carrier pigeon"]}).status_code == 422
    assert http.post("/alerts", json={"name": "Good fits", "min_fit": 7}).status_code == 200, (
        "a match score is a thing to look for")


@needs_db
def test_an_account_has_at_most_its_alerts_ceiling(api):
    from libs import limits

    http, db, user = api
    limits.set_limit(db, user.id, "alerts_per_account", 3)
    for index in range(3):
        assert http.post("/alerts", json={**LEGO, "name": f"Alert {index}"}).status_code == 200

    refused = http.post("/alerts", json=LEGO)
    assert refused.status_code == 409 and "Delete one" in refused.json()["detail"]
    limits.set_limit(db, user.id, "alerts_per_account", None)
    assert http.post("/alerts", json=LEGO).status_code == 200, "unlimited is unlimited"


@needs_db
def test_an_alerts_page_lists_what_it_found_and_counts_it(api):
    from libs.db.models import User
    from libs.db.models.tenant import record_state

    http, db, user = api
    alert = http.post("/alerts", json={**LEGO, "places": [], "sources": []}).json()
    found = _posting(db, "Brick Designer", company="The LEGO Group")
    skipped = _posting(db, "Brick Sorter", company="The LEGO Group")
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"active": True}})
    from libs.db.models import Alert
    db[Alert.COLLECTION].update_one({"_id": alert["id"]}, {"$set": {"created_at": now() - timedelta(days=1)}})
    _catch(db)
    record_state(db, user.id, skipped.id, dismissed_at=now())

    jobs = http.get(f"/alerts/{alert['id']}/jobs").json()
    assert [job["id"] for job in jobs] == [found.id], "less the one they skipped"
    [listed] = http.get("/alerts").json()["alerts"]
    assert (listed["found"], listed["last_found_at"] is not None) == (2, True)


@needs_db
def test_an_alert_says_what_it_would_have_found_this_week_as_it_is_saved(api):
    """Said as it is made or changed (owner, 2026-11-17), so an alert that will find nothing is seen to then
    rather than after days of silence: the postings of the last week it catches, by the rule that sends them,
    and for Starter the week its own list had, three days behind."""
    from libs.db.models import User

    http, db, user = api
    company = f"Brickworks {uuid.uuid4().hex[:6]}"
    for days in (1, 2, 5, 11):
        _posting(db, f"Brick Designer {days}", company=company, minutes_ago=days * 24 * 60)
    _posting(db, "Brick Designer", company="Somebody Else")
    bricks = {"name": "Bricks", "companies": [company]}

    made = http.post("/alerts", json=bricks).json()
    assert made["would_have_found"] == 3, "this week's three, not the one before it nor another employer's"
    nobody = http.put(f"/alerts/{made['id']}", json={**bricks, "companies": ["Nobody Hires Here"]}).json()
    assert nobody["would_have_found"] == 0
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"subscription_plan": "starter"}})
    assert http.put(f"/alerts/{made['id']}", json=bricks).json()["would_have_found"] == 1, (
        "Starter's list had only the five-day-old one this week")


@needs_db
def test_somebody_elses_alert_is_not_there(api):
    from libs.db.models import Alert

    http, db, _ = api
    stranger, _ = _person(db)
    [theirs] = _alerts(db, stranger)

    assert http.put(f"/alerts/{theirs.id}", json=LEGO).status_code == 404
    assert http.delete(f"/alerts/{theirs.id}").status_code == 404
    assert http.get(f"/alerts/{theirs.id}/jobs").status_code == 404
    assert db[Alert.COLLECTION].count_documents({"_id": theirs.id}) == 1


@needs_db
def test_every_account_from_before_named_alerts_keeps_its_alerts_once(db):
    """Nobody's alerts stop on the day they became named (`libs.alerts.alerts_for_existing`): each account
    gets My search, sending where theirs went, the daily email too if it was on; and it happens once ever."""
    from libs.alerts import alerts_for_existing
    from libs.db.models import User

    emailed = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x"))
    db[User.COLLECTION].update_one({"_id": emailed.id}, {"$set": {"email_alerts": True}})
    plain = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x"))

    assert alerts_for_existing(db) == 2
    [(mine, theirs)] = [(_alerts(db, emailed), _alerts(db, plain))]
    assert [(alert.name, alert.search, alert.channels) for alert in mine] == [
        ("My search", True, ["bell", "push", "telegram", "email"])]
    assert [alert.channels for alert in theirs] == [["bell", "push", "telegram"]]
    insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x"))
    assert alerts_for_existing(db) == 0, "once ever: an account made later gets its own at sign-up"


@needs_db
def test_signing_up_starts_an_account_with_my_search(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    from fastapi.testclient import TestClient

    from libs.db.models import User
    from libs.legal import LEGAL_VERSION
    from services.api.app import app

    email = f"{uuid.uuid4().hex[:10]}@example.com"
    with TestClient(app, base_url="https://testserver") as http:
        answered = http.post("/auth/signup", json={"email": email, "password": "a-long-Passw0rd-here!",
                                                   "consent_version": LEGAL_VERSION})
    assert answered.status_code < 300, answered.text
    user = User.from_doc(db[User.COLLECTION].find_one({"email": email}))
    assert [(alert.name, alert.search) for alert in _alerts(db, user)] == [("My search", True)]
