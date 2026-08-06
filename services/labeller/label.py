"""Scoring jobs against a user's rubric with Haiku. The training data everything else depends on.

**Why this exists as its own step.** A profile change bumps `profile_generation`, which makes every
label the user has stale by definition — a label written under an old rubric answers a different
question. Without a relabeller a re-process leaves zero usable labels at the new generation and
training cannot run at all, so this is the step that makes profile editing survivable rather than
catastrophic (`ONBOARDING_AND_PROCESSING.md` §4).

It is also **the most expensive thing on the platform**, per user, by an order of magnitude: one
Haiku call per job at ~$0.0018 (`COLD_START_PLAN.md` §4), against thousands of jobs. That single
fact drives most of what follows — batching, a description cap, a per-invocation ceiling, and
round-robin fairness so one user's four-thousand-job relabel cannot stall everybody else's.

**Consistency over cleverness.** The rubric tells the model to score each job independently rather
than relative to the batch, because a job of a given quality must get the same score no matter what
it was batched with. A label set scored relative to its batch is noise wearing a number.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import CorpusJob, User, UserLabel
from libs.llm import LlmClient, LlmError
from libs.obs import audit
from libs.scoring.rules import DESC_CHARS

logger = logging.getLogger(__name__)

# Cheap and consistent is what a labeller needs; the ladder in `MODEL_SELECTION_RESEARCH.md` §8 is
# Haiku, then batch, then cache, and only then a different model — with a relabel A/B to justify it.
LABEL_MODEL = "claude-haiku-4-5"

# Jobs per call. 50 is the reference instance's measured ceiling: ask for more scores in one
# response and the output truncates, which loses the tail of the batch silently.
BATCH_SIZE = 50


# **One queue job is one batch**, deliberately equal to `BATCH_SIZE`.
#
# The worker commits the handler's effect and the job's completion together and forbids a handler
# from committing its own transaction (`libs/queue/worker.py`), so a queue job's size *is* the unit
# of work that survives a crash. At 200 an interrupted worker would throw away four Haiku calls the
# user paid for; at 50 it throws away one. The re-enqueue loop makes progress either way, and it
# also lets round-robin interleave two users' relabels rather than running one to completion.
MAX_PER_RUN = BATCH_SIZE

# Output brevity lives here rather than in the rubric template on purpose: `profile_version` is the
# template's hash, so a wording change there re-versions **every user** and invalidates their labels.
# Instructions that do not change scoring semantics must never touch that hash.
SYSTEM_PROMPT = ("You are a precise scoring engine. Follow the user's rubric exactly. Output ONLY "
                 "the requested JSON array, minified with no whitespace. Keep every fit_notes "
                 "under 15 words.")

# One score object per job, and the batch is an array of them. Constrained rather than requested:
# "return only JSON" is a wish, a schema is a guarantee.
#
# **Two constraints the API imposes on structured output, both learned the hard way.**
#
# `additionalProperties: false` is required on *every* object, including nested ones. Without it the
# request is rejected with a 400 before a token is generated — which is what every labelling call
# was doing until 2026-07-29, discovered only when the shadow pass made the lane run again.
#
# `minimum` and `maximum` are **not** supported and are rejected the same way, so the 0–100 range
# lives in the rubric's own words instead. That is a real loss of a guarantee, and it is why
# `write_labels` coerces with `int()` rather than trusting the number it is handed.
SCORE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "url": {"type": "string"},
                    "score": {"type": "integer"},
                    "score_breakdown": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "technical": {"type": "integer"},
                            "experience": {"type": "integer"},
                            "behavioral": {"type": "integer"},
                            "career_alignment": {"type": "integer"},
                            "location": {"type": "string", "enum": ["PASS", "FAIL"]},
                        },
                        "required": ["technical", "experience", "behavioral", "career_alignment",
                                     "location"],
                    },
                    "fit_notes": {"type": "string"},
                },
                "required": ["url", "score"],
            },
        },
    },
    "required": ["scores"],
}


def stale_job_ids(session: Session, user_id: _uuid.UUID, limit: int = MAX_PER_RUN) -> list[int]:
    """Jobs this user has a label for at an **older** generation, oldest first.

    Only jobs already labelled. Labelling something new is *cold start* (`COLD_START_PLAN.md` §4),
    which selects a pool for balance and boundary; this is the narrower job of carrying an existing
    label set across a rubric change, and conflating the two would quietly spend a user's money
    labelling jobs nobody asked about.
    """
    user = session.get(User, user_id)
    if user is None:
        return []

    labelled_now = (
        select(UserLabel.job_id)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation == user.profile_generation))
    return list(session.scalars(
        select(UserLabel.job_id)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation < user.profile_generation,
               UserLabel.job_id.notin_(labelled_now))
        .order_by(UserLabel.job_id)
        .limit(limit)).all())


def _payload(job: CorpusJob) -> dict:
    """Only the fields the rubric scores on. Everything else is tokens the user pays for."""
    return {"url": job.url, "title": job.title or "", "company": job.company or "",
            "location": job.location or "", "description": (job.description or "")[:DESC_CHARS]}


def score_batch(client: LlmClient, rubric_md: str, jobs: list[CorpusJob]) -> dict[str, dict]:
    """One call. Returns `{url: score object}` for whatever came back.

    Keyed by URL rather than by position because a model that drops or reorders an element would
    otherwise shift every label onto the wrong job — a failure that produces a plausible-looking
    training set and is invisible until the model trained on it behaves strangely.
    """
    if not jobs:
        return {}

    import json  # noqa: PLC0415 - only needed on this path

    prompt = rubric_md.replace("<JOBS_JSON>", json.dumps([_payload(job) for job in jobs]))
    raw = client.complete_json(model=LABEL_MODEL, system=SYSTEM_PROMPT,
                               messages=[{"role": "user", "content": prompt}],
                               schema=SCORE_SCHEMA, max_tokens=8_000)

    by_url = {}
    for score in raw.get("scores") or []:
        url = score.get("url")
        if url and isinstance(score.get("score"), int):
            by_url[url] = score
    if len(by_url) < len(jobs):
        # Not fatal — the jobs that did come back are good labels — but never silent: a batch that
        # consistently returns 40 of 50 is a truncation problem, and it would otherwise look like
        # slow progress rather than a bug.
        logger.warning("batch returned %d scores for %d jobs", len(by_url), len(jobs))
    return by_url


def write_labels(session: Session, user_id: _uuid.UUID, scored: dict[str, dict],
                 jobs: list[CorpusJob]) -> int:
    """Insert one `llm` label per scored job at the user's **current** generation.

    Upserted on the primary key so a re-run is free rather than a duplicate-key crash: this worker
    can be interrupted mid-batch and the queue will replay the whole job.

    `profile_version` and `profile_generation` are copied onto each row at write time, which is what
    makes "these labels answer the current question" checkable later instead of assumed.
    """
    user = session.get(User, user_id)
    by_url = {job.url: job for job in jobs}
    written = 0

    for url, score in scored.items():
        job = by_url.get(url)
        if job is None:
            # The model invented or mangled a URL. Dropping it is right — attaching a score to a
            # guessed job is worse than having one fewer label.
            logger.warning("scored URL not in the batch, dropped")
            continue
        # **`fit_notes` is stored, not discarded.** The rubric asks for it in fifteen words —
        # what the role is, and why it did or did not match — and `rationale` has been on this
        # table since Phase 2 with nothing writing it. It is the scoring-time read that a CV build
        # later uses as its angle, which is the difference between a document written *toward*
        # something and one that keyword-matches a description (`REBUILD_PLAN.md`).
        notes = (score.get("fit_notes") or "").strip() or None
        session.execute(
            pg_insert(UserLabel)
            .values(user_id=user_id, job_id=job.id, label=int(score["score"]), source="llm",
                    weight=1.0, rationale=notes, profile_version=user.profile_version,
                    profile_generation=user.profile_generation)
            .on_conflict_do_update(
                index_elements=["user_id", "job_id"],
                set_={"label": int(score["score"]), "source": "llm", "rationale": notes,
                      "profile_version": user.profile_version,
                      "profile_generation": user.profile_generation}))
        written += 1
    return written


def relabel_user(session: Session, user_id: _uuid.UUID, client: LlmClient, *,
                 limit: int = MAX_PER_RUN) -> tuple[int, int]:
    """Relabel one batch of stale jobs. Returns (written, remaining).

    **Does not commit** — the worker owns the transaction, so that the labels written and the
    "job done" record land together or neither does (`libs/queue/worker.py`). `remaining` is what
    the worker re-enqueues on, which turns a four-thousand-job relabel into eighty small queue jobs
    that interleave with everybody else's rather than one that runs for an hour.
    """
    user = session.get(User, user_id)
    if user is None or not user.rubric_md:
        raise LlmError("this account has no rubric — confirm a profile first")

    job_ids = stale_job_ids(session, user_id, limit)
    if not job_ids:
        return 0, 0

    jobs = list(session.scalars(select(CorpusJob).where(CorpusJob.id.in_(job_ids))).all())
    written = write_labels(session, user_id, score_batch(client, user.rubric_md, jobs), jobs)
    session.flush()

    remaining = session.scalar(
        select(func.count()).select_from(UserLabel)
        .where(UserLabel.user_id == user_id,
               UserLabel.profile_generation < user.profile_generation)) or 0
    audit(logger, "relabel.batch", user=user_id, written=written, remaining=remaining,
          generation=user.profile_generation)
    return written, remaining
