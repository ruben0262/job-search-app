"""What happened after "Mark as applied", in the person's own words (owner, 2026-11-03), and what those words
add up to across everyone: the platform's one measure of whether its CVs, cover letters, searches and
ATS check get people interviews and jobs.

An application moves forward through `STAGES`, skipping any (an offer can come without an interview),
or ends at "rejected" from wherever it stands. The last step can always be taken back, for a tap made
by mistake. Each step is kept with its time, on the person's own row for the job (`UserJobState`).
"""
from __future__ import annotations

import uuid
from collections import Counter

from pymongo.database import Database

from libs.db import find_one, now
from libs.db.models import BuiltDocument, UserJobState

STAGES = ("interview", "offer", "hired")
REJECTED = "rejected"
# The group of `outcomes` that counts every application.
ALL = "All applications"


class StepRefused(ValueError):
    """The step does not follow from where the application stands; the message says why."""


def stage_of(progress: list[dict]) -> str:
    """Where an application stands: its last step, or "applied" before any."""
    return progress[-1]["stage"] if progress else "applied"


def advance(db: Database, user_id: uuid.UUID, job_id: int, stage: str) -> list[dict]:
    """Add a step, and return the whole progress."""
    row = find_one(db, UserJobState, {"user_id": user_id, "job_id": job_id})
    if row is None or row.applied_at is None:
        raise StepRefused("mark the job as applied to first")
    current = stage_of(row.progress)
    if current in ("hired", REJECTED):
        raise StepRefused("this application has ended — take the last step back to change it")
    if stage != REJECTED and STAGES.index(stage) <= (STAGES.index(current) if current in STAGES else -1):
        raise StepRefused(f"this application is already at {current}")
    step = {"stage": stage, "at": now()}
    # Only if nobody changed it meanwhile (a second tab): a step added twice is a step said twice. "Has
    # exactly this many steps" as "no step at this index, and one at the index before", because a row
    # saved before 2026-11-03 has no `progress` at all, and `$size: 0` never matches a field not there.
    count = len(row.progress)
    exactly = {f"progress.{count}": {"$exists": False}, **({f"progress.{count - 1}": {"$exists": True}}
                                                         if count else {})}
    changed = db[UserJobState.COLLECTION].update_one(
        {"user_id": user_id, "job_id": job_id, **exactly}, {"$push": {"progress": step}}).modified_count
    if not changed:
        raise StepRefused("this application changed meanwhile — refresh to see it")
    return [*row.progress, step]


def step_back(db: Database, user_id: uuid.UUID, job_id: int) -> list[dict]:
    """Take the last step back, and return what is left."""
    db[UserJobState.COLLECTION].update_one({"user_id": user_id, "job_id": job_id}, {"$pop": {"progress": 1}})
    row = find_one(db, UserJobState, {"user_id": user_id, "job_id": job_id})
    return row.progress if row else []


def outcomes(db: Database, user_id: uuid.UUID | None = None) -> list[dict]:
    """How far everyone's applications got, or one person's, all together and by the CV sent (none made
    here, instant or AI, passed or flagged by the ATS check): `{"group", "applied", "interview", "offer",
    "hired", "rejected"}`, where a stage counts every application that reached it or went past it.

    The CV is the newest one still kept for the job, so one somebody deleted reads as none made here.
    """
    whose = {} if user_id is None else {"user_id": user_id}
    cvs = {(row["user_id"], row["job_id"]): row for row in db[BuiltDocument.COLLECTION].find(
        {"kind": "cv", **whose}, {"user_id": 1, "job_id": 1, "mode": 1, "ats_ok": 1},
        sort=[("created_at", 1)])}
    groups: dict[str, Counter] = {}
    for row in db[UserJobState.COLLECTION].find({"applied_at": {"$ne": None}, **whose},
                                                {"user_id": 1, "job_id": 1, "progress": 1}):
        reached = {step["stage"] for step in row.get("progress") or []}
        furthest = max((STAGES.index(stage) for stage in reached if stage in STAGES), default=-1)
        counts = Counter(applied=1, rejected=int(REJECTED in reached),
                         **{stage: int(furthest >= index) for index, stage in enumerate(STAGES)})
        cv = cvs.get((row["user_id"], row["job_id"]))
        made = "No CV made here" if cv is None else ("AI-written CV" if cv["mode"] == "ai" else "Instant CV")
        names = [ALL, made]
        if cv is not None and cv.get("ats_ok") is not None:
            names.append("CV passed the ATS check" if cv["ats_ok"] else "CV flagged by the ATS check")
        for name in names:
            groups.setdefault(name, Counter()).update(counts)
    return [{"group": name, **{key: counts[key] for key in ("applied", *STAGES, REJECTED)}}
            for name, counts in groups.items()]
