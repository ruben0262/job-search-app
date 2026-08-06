"""Turn what a user actually did into training labels."""
from __future__ import annotations

import argparse
import sys
import uuid as _uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from libs.db.models import User, UserJobState, UserLabel
from libs.db.session import session_scope
from libs.feedback.actions import APPLIED_LABEL, FEEDBACK_WEIGHT, SKIPPED_LABEL


def _rows(session, user_id: _uuid.UUID, column, label: int) -> list[tuple[int, int]]:
    """`(job_id, label)` for every job this user acted on in one way."""
    return [(job_id, label) for job_id in session.scalars(
        select(UserJobState.job_id)
        .where(UserJobState.user_id == user_id, column.is_not(None)))]


def backfill_user(session, user: User, *, apply: bool) -> dict:
    """Report — and optionally write — one account's feedback labels."""
    pairs = (_rows(session, user.id, UserJobState.applied_at, APPLIED_LABEL)
             + _rows(session, user.id, UserJobState.dismissed_at, SKIPPED_LABEL))

    existing = dict(session.execute(
        select(UserLabel.job_id, UserLabel.source).where(UserLabel.user_id == user.id)).all())
    replacing = sum(1 for job_id, _ in pairs if existing.get(job_id) == "llm")
    already = sum(1 for job_id, _ in pairs if existing.get(job_id) == "feedback")

    if apply and pairs:
        for job_id, label in pairs:
            session.execute(
                pg_insert(UserLabel)
                .values(user_id=user.id, job_id=job_id, label=label, source="feedback",
                        weight=FEEDBACK_WEIGHT, profile_version=user.profile_version,
                        profile_generation=user.profile_generation)
                .on_conflict_do_update(
                    index_elements=["user_id", "job_id"],
                    set_={"label": label, "source": "feedback", "weight": FEEDBACK_WEIGHT,
                          "profile_version": user.profile_version,
                          "profile_generation": user.profile_generation}))

    return {"email": user.email, "total": len(pairs),
            "applied": sum(1 for _, label in pairs if label == APPLIED_LABEL),
            "dismissed": sum(1 for _, label in pairs if label == SKIPPED_LABEL),
            "replacing_llm": replacing, "already_feedback": already}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the labels; without it nothing is changed")
    parser.add_argument("--user", help="one account's uuid, rather than every account")
    args = parser.parse_args(argv)

    with session_scope() as session:
        query = select(User)
        if args.user:
            query = query.where(User.id == _uuid.UUID(args.user))
        reports = [backfill_user(session, user, apply=args.apply)
                   for user in session.scalars(query)]

        for report in reports:
            if not report["total"]:
                continue
            print(f"  {report['email']:32} {report['total']:4} labels "
                  f"({report['applied']} applied, {report['dismissed']} dismissed) — "
                  f"{report['replacing_llm']} replacing an LLM guess, "
                  f"{report['already_feedback']} already feedback")

        if not args.apply:
            print("\nNothing written. Re-run with --apply to commit these.")
        else:
            print("\nWritten. The next retrain consumes them — a retrain is not automatic here, "
                  "because a label backfill is exactly when somebody should look first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
