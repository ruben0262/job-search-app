"""Delivering one user's feed to their own Telegram bot.

Three rules, each of which is a silent failure when broken:

1. **Each `notified_at` commits the moment its message has gone**, one at a time. The queue is
   at-least-once, so a retry must re-send nothing already delivered (`SCHEDULER_PLAN.md` §4).

   This used to say the marks and the job's completion commit *together*, on the reasoning that
   splitting them leaves a window where the send happened and the record did not. The window is
   real and unavoidable — a Telegram message is not transactional — but batching does not close it,
   it widens it to the whole digest. Marking at the end meant a run that failed, or was swept back
   to `ready` for overrunning its two-minute claim, rolled back every mark while every message had
   already been delivered; the retry then sent the lot again. Measured on the live box: 15 of 319
   notify jobs overran, the worst at 638 seconds, and the account saw a flood of duplicates
   followed by silence. Per-message commits narrow the window from two hundred messages to one
   (ROADMAP 6ai).
2. **A revoked bot is terminal, not a retry.** Telegram answers 403 and it will never succeed.
   Retrying it five times wastes the attempts and then goes quiet anyway, so the user's feed stops
   with no signal to anyone. The account is flagged `broken`, the dispatcher stops enqueuing, and
   the fix is a reconnect email (G6).
3. **The token never leaves the worker.** It is decrypted inside `SecretStore.use`, held for the
   duration of the call, and scrubbed from anything logged in that window — including a traceback
   from the HTTP client, which is exactly where a failed request puts it.

Each user brings their own bot token, so Telegram's per-bot rate limits do not pool across the
platform. The per-user throttle here is to stop one badly-configured account spamming its owner.
"""
from __future__ import annotations

import logging
import statistics
import uuid as _uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import libs.notify as notifylib
from libs import limits
from libs.db.models import CorpusJob, User, UserConfig, UserJobState
from libs.db.models.queue import CLAIM_TIMEOUT_MIN
from libs.obs import audit
from libs.scoring.thresholds import DEFAULT_CUTOFF, cutoff_for
from libs.secrets import SecretsError, SecretStore, redacting
from services.notifier.buttons import keyboard
from services.notifier.card import FeedItem
from services.notifier.card import render as render_card
from services.notifier.eligibility import CANDIDATE_MULTIPLIER, Eligibility
from services.notifier.eligibility import select as select_feed
from services.notifier.freshness import REFRESH_BUDGET_SECONDS, date_candidates
from services.notifier.routing import group_by_chat

logger = logging.getLogger(__name__)

# Telegram allows ~30 messages/second to different chats but throttles a single chat much harder.
# One digest message per delivery keeps this simple; the cap is the per-day one below.
MAX_ITEMS_PER_DIGEST = 10

# What "unlimited jobs per run" means to a query that needs a number.
#
# **Not "everything at once", and the difference stopped an account delivering.** The limit's own
# description is "anything over stays queued for the next one; nothing is dropped" — so unlimited
# means the platform withholds nothing deliberately, not that one run must clear a backlog of 1,282
# in a single transaction. At 10,000 the candidate pool became five times that, and the ranking,
# eligibility and salary parsing over it did not finish inside the `notify` lane's two-minute claim:
# the job was reclaimed, re-ran, and that account received nothing at all while every job reported
# done.
#
# 200 is roughly twenty digests' worth. A backlog drains over a few cycles rather than never.
UNLIMITED_ITEMS = 200

# **How long the sending loop may run, derived from the claim it has to finish inside.**
#
# This is the fix for the failure the owner described as "jobs going on and off". Sending is paced
# at `send_gap_seconds` — 3 on both live accounts — so a run of 200 items takes 600 seconds against
# a `notify` claim of 120. Measured over 48 hours on the live box: **15 of 319 notify jobs overran
# their claim, the worst running 638 seconds.** Every one of those was swept back to `ready` while
# its worker was still sending, a second worker claimed it and started the same digest from the
# top, and the first worker's marks were rolled back when its transaction finally failed. The
# account got a flood of duplicates and then silence (ROADMAP 6ai).
#
# **Derived, not chosen.** A constant here would be a second definition of the claim timeout, which
# is the defect this project ships most often — so it reads the lane's own number and leaves room
# for the refresh that runs before it plus a margin for rendering. Whatever does not fit goes next
# cycle, which is exactly what the `items_per_run` limit already promises: "anything over stays
# queued for the next one; nothing is dropped".
SEND_BUDGET_SECONDS = (
    CLAIM_TIMEOUT_MIN["notify"] * 60 - REFRESH_BUDGET_SECONDS) * 0.6

# Re-exported, not redefined. The number is `libs.scoring.thresholds`, because the API's jobs table
# needs the same one and `services` may not import `services` — and three copies of a threshold are
# three chances for it to drift, which had already happened twice.

# Telegram's answers when the bot is gone. Distinct from a transient failure in every way that
# matters: no amount of retrying changes them.
TERMINAL_ERRORS = ("bot was blocked", "chat not found", "user is deactivated",
                   "bot was kicked", "unauthorized")


class BotRevoked(RuntimeError):
    """The user's bot is gone. Terminal — flag the account, do not retry."""


def pending_feed(session: Session, user_id: _uuid.UUID, *, cutoff: float,
                 limit: int = MAX_ITEMS_PER_DIGEST,
                 rules: Eligibility | None = None,
                 store: SecretStore | None = None) -> tuple[list[FeedItem], dict[str, int]]:
    """This user's next digest: eligible, ranked by opportunity, capped at `limit`.

    Returns the items and a tally of why the rest were dropped.

    `notified_at IS NULL` is the dedup, and it is a per-user column — the same posting can be new to
    one user and already sent to another, which is the whole point of a shared corpus with a
    per-user tail.

    **The database ordering is not the delivery ordering.** Score orders the *candidate pool* only,
    because "which jobs might be worth sending" is a fit question. Which of them actually goes out
    is an opportunity question — youth and scarcity — and `eligibility.select` answers it. Sorting
    by score and taking ten put a three-week-old posting with two hundred applicants ahead of one
    posted an hour ago.

    The pool is `CANDIDATE_MULTIPLIER` times the digest so that filtering cannot starve it: taking
    ten and then dropping four for language would send six when forty were eligible.
    """
    if rules is None:
        config = session.scalar(select(UserConfig.config).where(UserConfig.user_id == user_id))
        # The applicant ceiling is the operator's, not the user's: it comes from `libs.limits`
        # alongside every other per-account ceiling, and `None` there means show everything.
        user = session.get(User, user_id)
        rules = Eligibility.from_config(
            config, applicant_cap=limits.resolve(user, "applicant_cap") if user else None)

    # **The age filter belongs in the query, not only after it.** The pool is ordered by score, so
    # without this a backlog of stale high-scoring rows fills every candidate slot and is then
    # dropped for being old — leaving nothing to send while hundreds of fresh, eligible jobs sit
    # one page further down. That is precisely what happened: 50 candidates, 50 dropped, zero sent,
    # with 25 fresh matches waiting.
    #
    # **An undated row is admitted only while we have not held it long.** It used to be admitted
    # unconditionally, on the reasoning that `date_candidates` would resolve it — but that needs the
    # account's own proxy, and an account without one can never date anything. Measured on the live
    # box: one account's candidate pool was eleven undated rows, all first seen more than a day
    # earlier, re-selected and re-dropped as `too_old` on every single cycle for hours, with nothing
    # else able to get in behind them. The same sixteen counts in the log, tick after tick
    # (ROADMAP 6ay).
    #
    # `first_seen_at` is the fallback `too_old` already uses in Python, so this is the same rule
    # asked in the same shape rather than a second one — which is what the two of them being
    # different made possible.
    cutoff_at = datetime.now(UTC) - timedelta(hours=rules.max_age_hours)

    rows = session.execute(
        select(UserJobState.job_id, CorpusJob.url, CorpusJob.title, CorpusJob.company,
               UserJobState.score, CorpusJob.source, CorpusJob.location, CorpusJob.is_remote,
               CorpusJob.description, CorpusJob.posted_at, CorpusJob.applicants,
               # **Positional.** `FeedItem(*row[:-1])` below maps these by order, so a column
               # inserted here has to go in at the same place in the dataclass — and a mismatch is
               # silent, producing cards with fields shifted by one.
               CorpusJob.applicants_caption,
               CorpusJob.easy_apply, CorpusJob.reposted, CorpusJob.workplace,
               CorpusJob.first_seen_at)
        .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
        .where(UserJobState.user_id == user_id,
               # Never a closed posting. Filtered in SQL rather than in `eligibility` because it is
               # not a judgement about the user's preferences — there is nothing to apply for.
               CorpusJob.closed_at.is_(None),
               UserJobState.notified_at.is_(None),
               UserJobState.score.is_not(None),
               UserJobState.score >= cutoff,
               (CorpusJob.posted_at >= cutoff_at)
               | (CorpusJob.posted_at.is_(None) & (CorpusJob.first_seen_at >= cutoff_at)))
        # **Known-fresh first, then undated, and only then by score.** The age filter above lets
        # undated rows through because it must — LinkedIn reports no posted time and dating them is
        # what `date_candidates` is for — but ordering the pool by score alone let them take every
        # slot: they are the bulk of the corpus, they are indistinguishable from fresh until
        # somebody fetches them, and only a handful can be dated per run. Observed on 2026-07-31,
        # after the model was fixed and 26 genuinely fresh matches were waiting: 50 candidates
        # selected, 50 dropped as `{'too_old': 28, 'closed': 22}`, zero sent, twice in a row.
        #
        # Every *dated* row here already passed the freshness window in SQL, so preferring them
        # costs nothing and guarantees the pool contains the jobs we can prove are recent. Undated
        # rows still fill the rest and are still dated — they are ranked behind certainty, not
        # excluded by it.
        .order_by(CorpusJob.posted_at.is_(None), UserJobState.score.desc())
        .limit(limit * CANDIDATE_MULTIPLIER)).all()

    items = [FeedItem(*row[:-1]) for row in rows]
    first_seen = {row[0]: row[-1] for row in rows}

    # **Date the front of the queue before filtering it.** The age filter can only drop what it can
    # date, and LinkedIn reports no posted time to the listing API at all — so the filter was
    # failing open on most of the feed, which is how "Reposted 1 month ago" kept arriving as a live
    # card. `store` is optional so every existing caller and test keeps working undated.
    if store is not None:
        with _fetching_as(store, user_id) as client:
            if client is not None:
                items = date_candidates(session, items, client)

    return select_feed(items, rules, limit=limit, first_seen=first_seen)


@contextmanager
def _fetching_as(store: SecretStore, user_id: _uuid.UUID):
    """An HTTP client on this user's own proxy, or `None` if they have none.

    Their proxy or nothing — LinkedIn's guest endpoint refuses datacenter addresses, so an
    unproxied attempt is a request that cannot succeed. `None` rather than an unproxied client
    makes the caller skip instead of spending a round trip to learn that.

    A secret store that cannot answer is caught rather than propagated: dating the feed is an
    improvement to it, not a precondition for having one.
    """
    try:
        with store.use(user_id, "proxy") as secret:
            if secret is None:
                yield None
                return
            proxy = secret.reveal()
            with redacting(proxy), httpx.Client(timeout=25, follow_redirects=True,
                                                proxy=proxy) as client:
                yield client
        return
    except SecretsError:
        logger.warning("notify: no proxy available for %s, sending undated", user_id)
    yield None


def notifications_today(session: Session, user_id: _uuid.UUID) -> int:
    """How many items have been delivered since the user's local midnight.

    Counted in `users.timezone`, not UTC: "resets at midnight your time" has to keep meaning the
    same thing in November, and the platform's users are across several zones.
    """
    timezone = session.scalar(select(User.timezone).where(User.id == user_id)) or "UTC"
    local_midnight = func.date_trunc("day", func.timezone(timezone, func.now()))
    return session.scalar(
        select(func.count())
        .select_from(UserJobState)
        .where(UserJobState.user_id == user_id,
               UserJobState.notified_at.is_not(None),
               func.timezone(timezone, UserJobState.notified_at) >= local_midnight)) or 0


def deliver(session: Session, user_id: _uuid.UUID, *,
            store: SecretStore,
            send: Callable[..., None],
            cutoff: float = DEFAULT_CUTOFF) -> int:
    """Send this user's pending feed. Returns how many items were delivered.

    `send(token, chat_id, text, keyboard=None)` is injected so the transport is one replaceable
    function — the tests drive it, and a Telegram API change touches one place.

    **Marks `notified_at` in the same transaction the caller commits alongside the queue job.** The
    caller commits; this function deliberately does not, so the two cannot be split.
    """
    user = session.get(User, user_id)
    if user is None or user.telegram_status != "connected" or not user.telegram_chat_id:
        return 0

    cutoff = _operating_point(session, user_id, cutoff)

    # **The operator's ceilings for this account, and `None` means unlimited** (`libs.limits`). It
    # used to be a user-editable config value, which was the wrong owner: somebody who can raise
    # their own delivery ceiling can get their own Telegram bot rate-limited. Unlimited is safe now
    # only because sending is paced below — the cap used to be the sole guard against that, and it
    # guarded by withholding jobs the user had matched.
    #
    # **`None` means unlimited and must not be given a fallback.** `resolve` already returns the
    # catalogue default for a limit nobody has set, so `None` is only ever the operator having
    # chosen Unlimited — and `resolve(...) or DEFAULT` turned that choice back into the default.
    # An account set to Unlimited jobs per run was silently sending ten, which is what the setting
    # was changed to stop.
    per_day = limits.resolve(user, "notifications_per_day")
    # Never None: `send_gap_seconds` is Telegram's rule rather than ours and forbids unlimited.
    gap = limits.resolve(user, "send_gap_seconds") or limits.DEFAULT_SEND_GAP_SECONDS
    per_run = limits.resolve(user, "items_per_run")

    if per_day is not None:
        remaining = per_day - notifications_today(session, user_id)
        if remaining <= 0:
            # Over cap is not an error and not a silent drop: the items keep `notified_at IS NULL`
            # and go out in the next window.
            logger.info("notify: %s is at their daily cap, deferring", user_id)
            return 0
        # A daily ceiling bounds an unlimited per-run one; two unlimiteds stay unlimited.
        per_run = remaining if per_run is None else min(per_run, remaining)

    items, dropped = pending_feed(session, user_id, store=store, cutoff=cutoff,
                                  limit=per_run if per_run is not None else UNLIMITED_ITEMS)
    if dropped:
        # Counted by reason, because "delivered 2" is alarming and unactionable while "dropped 38
        # for language" is a config problem somebody can fix in a minute.
        logger.info("notify: %s dropped %s before ranking", user_id, dropped)
    if not items:
        return 0

    # One digest per channel. A user who has split their feed gets a Hungarian batch in the HU
    # channel and a LinkedIn batch in the LinkedIn one; a user who has not gets exactly one message
    # to their personal chat, which is the same behaviour as before this existed.
    batches = group_by_chat(items, user.telegram_routes, user.telegram_chat_id)

    sent = 0
    deadline = monotonic() + SEND_BUDGET_SECONDS
    with store.use(user_id, "telegram_bot") as token:
        if token is None:
            raise BotRevoked(f"user {user_id} has no telegram token stored")
        for chat_id, batch in batches.items():
            try:
                # **Cards only, no header.** The reference feed posts one card per job and nothing
                # else; a "10 new matches" line above them was a platform addition that turns a
                # channel you scroll into a channel you have to read the top of.
                for item in batch:
                    # **Stop before the claim does.** Whatever is left keeps `notified_at IS NULL`
                    # and goes out next cycle — the same fail-forward the daily cap already uses.
                    if monotonic() >= deadline:
                        logger.info(
                            "notify: %s sent %d and ran out of its send budget — the rest go next "
                            "cycle", user_id, sent)
                        return sent
                    # **Paced, because Telegram allows 20 messages a minute to a channel.** The
                    # gap is what makes an unlimited daily ceiling safe: the old guard against
                    # rate limits was a cap, which protected the bot by withholding jobs the user
                    # had matched. This protects it by taking longer instead. No sleep before the
                    # first message — a run that sends one card should not wait to send it.
                    if sent:
                        sleep(gap)
                    send(token.reveal(), chat_id, render_job(item, cutoff=cutoff),
                         keyboard(item.job_id))
                    # **Marked and committed the moment it has gone, one message at a time.**
                    #
                    # A sent message cannot be un-sent, so its record must not depend on the rest
                    # of the run succeeding. Marking all of them at the end meant that a run which
                    # failed — or was swept back to `ready` for overrunning its claim — rolled back
                    # every mark while the messages were already delivered, and the retry sent the
                    # whole digest again. That is what a flood of duplicates followed by silence
                    # actually was (ROADMAP 6ai).
                    #
                    # Committing here narrows the at-least-once window from the whole digest to a
                    # single message: a crash between the send and the commit costs one duplicate
                    # rather than two hundred.
                    session.get(UserJobState, (user_id, item.job_id)).notified_at = func.now()
                    session.commit()
                    sent += 1
            except Exception as exc:
                if _is_terminal(exc):
                    # A revoked token is terminal for every channel, so stopping here is right.
                    # A single bad *channel* id is not — but Telegram answers 400 for that, which
                    # `_is_terminal` does not match, so it retries rather than marking the account
                    # broken.
                    user.telegram_status = "broken"
                    # The one failure Telegram itself cannot tell them about.
                    notifylib.notify(session, user_id, notifylib.TELEGRAM_BROKEN,
                                     "Your bot token stopped working, so jobs are not being "
                                     "delivered. Save a fresh token under Your details.")
                    raise BotRevoked(str(exc)[:200]) from exc
                raise

    _log_freshness(session, user_id, items[:sent])
    return sent


def _operating_point(session: Session, user_id: _uuid.UUID, requested: float) -> float:
    """The score this user's jobs are sent at.

    The model's own tuned value, not the platform constant — a caller that passed something else
    explicitly still wins, which is how a test pins one. The constant is the label threshold that
    decides what counts as "fit" while *training*, and using it to decide delivery is what emptied
    the feed: a model asked for 60 when its tuned answer was 44 sends almost nothing.
    """
    if requested != DEFAULT_CUTOFF:
        return requested
    return cutoff_for(session, user_id)


def _log_freshness(session: Session, user_id: _uuid.UUID, items: list[FeedItem]) -> None:
    """How stale the feed was, measured at the only point where the answer is real.

    **This is the number the product is judged on, and no stage duration predicts it.** A cycle
    whose every stage is fast still delivers a job that waited for the next fifteen-minute tick, and
    then for the user's own notify interval on top. Most of the latency is spent *between* stages,
    which is invisible to anything timing a stage.

    Two clocks, because they answer different questions and neither alone is enough:

    - **from posted** — what the user experiences. How old the job was when they heard about it,
      and the one that decides whether they are early to it.
    - **from first seen** — what this platform controls. Everything before we saw the posting
      belongs to the source, and holding ourselves to it would be measuring LinkedIn's crawl.
    """
    job_ids = [item.job_id for item in items]
    if not job_ids:
        return

    rows = session.execute(
        select(CorpusJob.posted_at, CorpusJob.first_seen_at)
        .where(CorpusJob.id.in_(job_ids))).all()
    now = datetime.now(UTC)

    def minutes(values) -> int | None:
        ages = [(now - value).total_seconds() / 60 for value in values if value is not None]
        return round(statistics.median(ages)) if ages else None

    # Median, not mean: one job the source dated three weeks ago drags a mean into meaninglessness,
    # and the question is what a typical card in this batch was worth.
    audit(logger, "notify.freshness", user=user_id, items=len(job_ids),
          median_age_from_posted_min=minutes(row[0] for row in rows),
          median_age_from_first_seen_min=minutes(row[1] for row in rows))


def render_job(item: FeedItem, salary_bands: dict | None = None,
               cutoff: float = DEFAULT_CUTOFF) -> str:
    """One job, as its own message, so it can carry its own buttons.

    The card is `services/notifier/card.py`, a port of the reference's `watcher/feed.py`. This stays
    as the seam `deliver` and the tests both call.

    `cutoff` is the same number this delivery was selected on, handed to the card so the 🟢 and the
    decision to send cannot disagree. They did: the card called anything under 80 borderline while
    delivery sent everything over 60.
    """
    return render_card(item, salary_bands=salary_bands, high_fit=cutoff)


def _is_terminal(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in TERMINAL_ERRORS)
