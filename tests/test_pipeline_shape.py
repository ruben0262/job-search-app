"""Where the jobs are — and whether the page telling you can be trusted."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from libs.db import insert


def _job(db, **kwargs):
    from libs.corpus.repository import MIN_DESCRIPTION
    from libs.db.models import CorpusJob

    # Whole text has `described_at`, as `set_description` gives it the moment the text arrives.
    if len(kwargs.get("description") or "") >= MIN_DESCRIPTION:
        kwargs.setdefault("described_at", kwargs.get("description_fetched_at") or datetime.now(UTC))

    # `source` is overridable: the per-source freshness floors are keyed on it, so a test about
    # retention has to be able to say which board a posting came from.
    return insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}",
                                source=kwargs.pop("source", "test"),
                                title="Backend Engineer", **kwargs))


def stage(report: dict, key: str) -> dict:
    return next(s for s in report["stages"] if s["key"] == key)


def test_every_stage_of_the_pipeline_is_present(db):
    """Missing stages are the failure this exists to prevent."""
    from libs.ops.pipeline import pipeline

    keys = [s["key"] for s in pipeline(db)["stages"]]
    # No separate "store", and no "embed" or "score": nothing embeds or scores (ROADMAP Phase 8). No "words"
    # since 2026-11-06: a job's words are found in the write that stores its text.
    assert keys == ["gather", "describe", "deliver"]
    for entry in pipeline(db)["stages"]:
        assert entry["label"] and entry["what"], "every stage says what it is, on the page itself"


def test_a_posting_whose_text_is_read_waits_for_nothing_more(db):
    """Its words are found in the same write as its text (owner, 2026-11-06). Found at the end of each run
    before, 689 read jobs waited for them at once, up to 15 minutes each."""
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    corpus = CorpusRepository(db)
    job_id = corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Backend Engineer")
    assert {entry["key"]: entry["waiting"] for entry in pipeline(db)["stages"] if entry["waiting"]} == {
        "describe": 1}

    corpus.set_description(job_id, "We build things. " * 40)
    assert all(not entry["waiting"] for entry in pipeline(db)["stages"])


def test_a_row_with_no_description_waits_at_describe(db):
    """Each stage counts what *it* is holding."""
    from libs.ops.pipeline import pipeline

    _job(db)

    assert stage(pipeline(db), "describe")["waiting"] == 1


# A trimmed row used to be the case here: retention nulled a description in place, leaving a row
# that read as waiting to be described forever. Retention deletes the row now, so there is no such
# state to exclude — the backlog cannot count what is not there. The closed-posting case below is
# the one that still needs asserting, because a closed row does stay in the corpus.


def test_the_verdict_is_about_age_not_size(db):
    """A thousand rows moving is fine; forty stuck for a day is not."""
    from libs.ops.pipeline import pipeline

    for _ in range(50):
        _job(db)

    assert stage(pipeline(db), "describe")["verdict"] == "ok", "fresh, however many"


def test_a_stage_holding_something_old_turns_red(db):
    """The other half: one row nobody has picked up for hours is the signal.

    Three hours rather than a day, because a day-old posting is past the delivery window and the
    describer no longer fetches it — see the test below. Red now means "inside the window and
    nobody has taken it", which is the condition an operator can actually act on.
    """
    from libs.ops.pipeline import pipeline

    _job(db, first_seen_at=datetime.now(UTC) - timedelta(hours=3))

    entry = stage(pipeline(db), "describe")
    assert entry["verdict"] == "down"
    assert entry["oldest_min"] > 60


def test_a_posting_too_old_to_deliver_is_not_reported_as_waiting(db):
    """It is not waiting for anything. Nothing will ever fetch it and nobody could receive it.

    Measured in production on 2026-08-08: the describer was retrying 212 thin LinkedIn rows every
    fifteen minutes, 156 of them already past the delivery window, the oldest first seen 167 hours
    earlier — the same ids, on a user's own metered proxy, for a week.
    """
    from libs import settings
    from libs.corpus.freshness import platform_windows
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    # These ages are read against a day's job shelf life; a week is the default (2026-11-07).
    settings.put(db, "job_max_age_hours", 24)
    stale = _job(db, description=None, first_seen_at=datetime.now(UTC) - timedelta(days=3))

    assert stage(pipeline(db), "describe")["waiting"] == 0
    assert not [row for row in CorpusRepository(db).awaiting_description(windows=platform_windows(db))
                if row.id == stale.id], "and the describer agrees"


def test_an_empty_stage_is_green_and_says_nothing_about_age(db):
    """`None` rather than zero. Zero minutes is a claim about a queue that does not exist."""
    from libs.ops.pipeline import pipeline

    entry = stage(pipeline(db), "describe")
    assert entry["waiting"] == 0
    assert entry["oldest_min"] is None
    assert entry["verdict"] == "ok"


def test_the_thresholds_come_from_the_tick_that_clears_them(db):
    """Derived, not chosen — the run every 15 minutes is what drains these steps, and Latency judges each
    by the same limits (`steps.THRESHOLDS`)."""
    from libs.ops.pipeline import pipeline
    from libs.settings import PLATFORM_TICK_MIN

    limits = {row["key"]: (row["slow_after_min"], row["too_long_after_min"])
              for row in pipeline(db)["stages"]}
    assert limits["describe"] == (PLATFORM_TICK_MIN, 4 * PLATFORM_TICK_MIN)
    assert limits["gather"] == limits["deliver"] == (None, None), "no queue, so nothing to wait too long in"


def test_a_step_turns_slow_then_too_long_by_its_oldest_waiting_job(db):
    from libs.ops.pipeline import pipeline
    from libs.settings import PLATFORM_TICK_MIN

    job = _job(db, first_seen_at=datetime.now(UTC) - timedelta(minutes=PLATFORM_TICK_MIN - 2))
    assert stage(pipeline(db), "describe")["verdict"] == "ok"
    for minutes, verdict in ((PLATFORM_TICK_MIN + 2, "warn"), (4 * PLATFORM_TICK_MIN + 2, "down")):
        db.corpus.update_one({"_id": job.id}, {"$set": {
            "first_seen_at": datetime.now(UTC) - timedelta(minutes=minutes)}})
        assert stage(pipeline(db), "describe")["verdict"] == verdict


def test_each_step_counts_exactly_what_went_through_it_in_the_window(db):
    from libs.ops.pipeline import pipeline

    moment = datetime.now(UTC)
    for hours_ago in (1, 2, 23):
        _job(db, first_seen_at=moment - timedelta(hours=hours_ago))
    _job(db, first_seen_at=moment - timedelta(hours=30))  # before the window
    _job(db, first_seen_at=moment - timedelta(hours=3), description="We build things. " * 40,
         description_fetched_at=moment - timedelta(hours=2), terms_at=moment - timedelta(hours=1))
    _job(db, first_seen_at=moment - timedelta(hours=40), description="We build things. " * 40,
         description_fetched_at=moment - timedelta(hours=30), terms_at=moment - timedelta(hours=29))

    entered = {row["key"]: row["entered"] for row in pipeline(db, hours=24)["stages"]}

    assert entered == {"gather": 4, "describe": 1, "deliver": 0}


def test_a_job_too_old_to_be_shown_leaves_the_text_queue_and_is_counted_once_as_missed(db):
    """The owner: a job past the window is dropped from describing, not left to pile up (2026-11-05). The
    page says how many were dropped untaken in the window, so a describer falling behind is seen."""
    from libs import settings
    from libs.ops.pipeline import pipeline

    # These ages are read against a day's job shelf life; a week is the default (2026-11-07).
    settings.put(db, "job_max_age_hours", 24)
    moment = datetime.now(UTC)
    for hours_ago in (1, 25, 50):  # waiting; aged out today; aged out the day before
        _job(db, source="linkedin", first_seen_at=moment - timedelta(hours=hours_ago))
    _job(db, source="linkedin", first_seen_at=moment - timedelta(hours=25), description="We build. " * 40)

    describe = stage(pipeline(db, hours=24), "describe")

    assert (describe["waiting"], describe["missed"]) == (1, 1)


def test_every_source_is_listed_with_what_it_found_and_a_silent_one_as_zero(db):
    """A board that stops answering raises nothing: this is where it shows."""
    from libs import settings
    from libs.ops.pipeline import sources
    from libs.settings import HARVEST_SOURCES

    # These ages are read against a day's job shelf life; a week is the default (2026-11-07).
    settings.put(db, "job_max_age_hours", 24)
    moment = datetime.now(UTC)
    _job(db, source="himalayas", first_seen_at=moment - timedelta(hours=1), description="We build. " * 40)
    _job(db, source="himalayas", first_seen_at=moment - timedelta(hours=2))
    _job(db, source="linkedin", first_seen_at=moment - timedelta(hours=1))
    _job(db, source="linkedin", first_seen_at=moment - timedelta(hours=30))  # before the window

    rows = {row["source"]: row for row in sources(db, hours=24)}

    assert set(HARVEST_SOURCES) <= set(rows)
    assert {key: rows["himalayas"][key] for key in ("found", "with_text", "waiting")} == {
        "found": 2, "with_text": 1, "waiting": 1}
    assert {key: rows["linkedin"][key] for key in ("found", "with_text", "waiting")} == {
        "found": 1, "with_text": 0, "waiting": 1}
    assert rows["jobicy"]["found"] == 0 and rows["jobicy"]["newest"] is None
    assert [row["source"] for row in sources(db, hours=24)][:2] == ["himalayas", "linkedin"], "busiest first"


# ── the three ways the first version lied ───────────────────────────────────────────────────────


def _account(db):
    from libs.db.models import User

    return insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                           active=True))


def _state(db, user, job, *, notified=False):
    from libs.db.models import UserJobState

    insert(db, UserJobState(user_id=user.id, job_id=job.id,
                            notified_at=datetime.now(UTC) if notified else None))


def test_delivery_shows_no_queue_and_counts_what_was_sent(db):
    """What is matched is decided when it is read, so a row nobody has sent is not a queue at delivery."""
    from libs.ops.pipeline import pipeline

    user = _account(db)
    _state(db, user, _job(db, description="x " * 200, posted_at=datetime.now(UTC) - timedelta(minutes=10)))
    _state(db, user, _job(db, description="x " * 200), notified=True)

    entry = stage(pipeline(db), "deliver")
    assert entry["waiting"] is None
    assert entry["verdict"] == "ok"
    assert entry["entered"] == 1, "what was sent in the window is counted, and only that"


def test_a_stage_with_no_queue_says_so_rather_than_claiming_zero(db):
    """Gathering and storing are one `INSERT`, so nothing is ever between them."""
    from libs.ops.pipeline import pipeline

    entry = stage(pipeline(db), "gather")
    assert entry["waiting"] is None
    assert entry["verdict"] == "ok"


def test_a_job_a_worker_is_running_is_counted_as_running(db):
    """The queue has no state called `running`."""
    from libs.db.models import QueueJob
    from libs.ops.pipeline import pipeline

    user = _account(db)
    insert(db, QueueJob(lane="notify", user_id=user.id, state="claimed",
                        claimed_at=datetime.now(UTC), claimed_by="worker-1"))

    notify = next(lane for lane in pipeline(db)["lanes"] if lane["lane"] == "notify")
    assert notify["running"] == 1


def test_the_page_and_the_describer_count_the_same_rows(db):
    """Same defect, same shape: a closed row is never fetched and must not read as waiting."""
    from libs.corpus.repository import CorpusRepository
    from libs.ops.pipeline import pipeline

    _job(db, description=None, closed_at=datetime.now(UTC))
    waiting = _job(db, description=None)

    stages = {stage["key"]: stage for stage in pipeline(db, hours=24)["stages"]}
    pending = CorpusRepository(db).awaiting_description(limit=1000)

    assert [row.id for row in pending] == [waiting.id], "the closed one is never fetched"
    assert stages["describe"]["waiting"] == len(pending)


def test_the_queue_says_which_account_each_lane_is_working_for(db):
    """"docgen 2 dead" is not actionable until you know whose two.

    A lane is per-account work. The chips summed across accounts, so an operator seeing dead jobs
    had to open psql against production to find the owner — which is how an account that could not
    build a CV went unnoticed for three days.
    """
    import uuid as _uuid

    from libs.db.models import QueueJob, User
    from libs.ops.pipeline import pipeline

    first, second = (insert(db, User(email=f"{_uuid.uuid4()}@example.com", password_hash="x",
                                     status="active")) for _ in range(2))
    insert(db, QueueJob(lane="docgen", user_id=first.id, state="dead",
                        last_error="AttributeError: 'NoneType' object has no attribute 'calls'"))
    insert(db, QueueJob(lane="docgen", user_id=second.id, state="ready"))

    rows = {(row["lane"], row["email"]): row for row in pipeline(db, hours=24)["lane_accounts"]}

    assert rows[("docgen", first.email)]["dead"] == 1
    assert "AttributeError" in rows[("docgen", first.email)]["last_error"]
    assert rows[("docgen", second.email)]["ready"] == 1
    assert rows[("docgen", second.email)]["dead"] == 0, "the two accounts are not conflated"


def test_an_account_shows_its_newest_failure_not_the_alphabetically_last(db, user):
    """`$max` of the error text alone picked "Zebra…" over a newer "Apple…"."""
    from libs.db import insert, now
    from libs.db.models import QueueJob
    from libs.ops.pipeline import lane_accounts

    insert(db, QueueJob(lane="docgen", user_id=user.id, state="dead", last_error="Zebra: the older failure"))
    insert(db, QueueJob(lane="docgen", user_id=user.id, state="dead", last_error="Apple: the newest failure"))
    insert(db, QueueJob(lane="docgen", user_id=user.id, state="done", finished_at=now()))

    [row] = lane_accounts(db, now() - timedelta(hours=24))
    assert row["last_error"] == "Apple: the newest failure"
    assert (row["dead"], row["done"]) == (2, 1)
