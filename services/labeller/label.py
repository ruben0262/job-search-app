"""Scoring jobs against a user's rubric with Haiku."""
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

# Cheap and consistent is what a labeller needs (`MODEL_SELECTION_RESEARCH.md` §8).
LABEL_MODEL = "claude-haiku-4-5"

# Jobs per call.
BATCH_SIZE = 50


# One queue job is one batch, deliberately equal to `BATCH_SIZE`.
MAX_PER_RUN = BATCH_SIZE

# Output brevity lives here, not in the template whose hash re-versions every user.
SYSTEM_PROMPT = ("You are a precise scoring engine. Follow the user's rubric exactly. Output ONLY "
                 "the requested JSON array, minified with no whitespace. Keep every fit_notes "
                 "under 15 words.")

# One score object per job, and the batch is an array of them.
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
    """Jobs this user has a label for at an older generation, oldest first."""
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
               # `bump_generation` carries the user's own answers forward, so one left behind here
               # is a row some older bump stranded. Buying a label for it would spend the account's
               # credential on a job whose answer we already have and are not allowed to replace.
               UserLabel.source != "feedback",
               UserLabel.job_id.notin_(labelled_now))
        .order_by(UserLabel.job_id)
        .limit(limit)).all())


def _payload(job: CorpusJob) -> dict:
    """Only the fields the rubric scores on. Everything else is tokens the user pays for."""
    return {"url": job.url, "title": job.title or "", "company": job.company or "",
            "location": job.location or "", "description": (job.description or "")[:DESC_CHARS]}


def score_batch(client: LlmClient, rubric_md: str, jobs: list[CorpusJob]) -> dict[str, dict]:
    """One call."""
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
        # Not fatal but never silent: a batch returning 40 of 50 is truncation, not slow progress.
        logger.warning("batch returned %d scores for %d jobs", len(by_url), len(jobs))
    return by_url


def write_labels(session: Session, user_id: _uuid.UUID, scored: dict[str, dict],
                 jobs: list[CorpusJob]) -> int:
    """Insert one `llm` label per scored job at the user's current generation."""
    user = session.get(User, user_id)
    by_url = {job.url: job for job in jobs}
    written = 0

    for url, score in scored.items():
        job = by_url.get(url)
        if job is None:
            # The model invented or mangled a URL.
            logger.warning("scored URL not in the batch, dropped")
            continue
        # `fit_notes` is stored, not discarded.
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
                      "profile_generation": user.profile_generation},
                # **A guess never replaces an answer.** `bump_generation` carries feedback labels
                # forward so they should never reach a batch — this is the guard that makes it true
                # regardless of which caller assembled the batch, because what it protects is the
                # only ground truth the platform has and one was already lost without it.
                where=UserLabel.source != "feedback"))
        written += 1
    return written


def relabel_user(session: Session, user_id: _uuid.UUID, client: LlmClient, *,
                 limit: int = MAX_PER_RUN) -> tuple[int, int]:
    """Relabel one batch of stale jobs."""
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
