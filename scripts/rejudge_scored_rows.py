"""Re-apply the pre-score filter to rows that were scored before it existed."""
from __future__ import annotations

import logging
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from libs import limits
from libs.db.models import CorpusJob, User, UserConfig, UserJobState
from libs.obs import configure
from libs.tenant.filters import UserFilters

logger = logging.getLogger("rejudge")


def rejudge(session: Session, *, apply: bool) -> dict[str, dict[str, int]]:
    """Every unsent scored row, re-asked. Returns the tally per account."""
    tally: dict[str, dict[str, int]] = {}

    for user in session.scalars(select(User).where(User.active)):
        cap = limits.resolve(user, "applicant_cap")
        filters = UserFilters.from_config(
            session.scalar(select(UserConfig.config).where(UserConfig.user_id == user.id)),
            applicant_cap=None if cap is None else int(cap))

        rows = session.execute(
            select(UserJobState.job_id, CorpusJob.title, CorpusJob.company, CorpusJob.source,
                   CorpusJob.applicants, CorpusJob.location, CorpusJob.is_remote,
                   CorpusJob.workplace, CorpusJob.description)
            .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
            # Unsent only.
            .where(UserJobState.user_id == user.id,
                   UserJobState.notified_at.is_(None),
                   UserJobState.score.is_not(None))).mappings().all()

        counts: dict[str, int] = {}
        for row in rows:
            reason = filters.unwanted(row)
            if reason is None:
                continue
            counts[reason] = counts.get(reason, 0) + 1
            if apply:
                state = session.get(UserJobState, (user.id, row["job_id"]))
                state.score = None

        tally[user.email] = counts
        logger.info("rejudge: %s considered %d, would drop %s", user.email, len(rows), counts or {})

    if apply:
        session.commit()
    return tally


def main(argv: list[str] | None = None) -> int:
    configure("rejudge")
    apply = "--apply" in (argv if argv is not None else sys.argv[1:])
    with Session(create_engine(os.environ["DATABASE_URL"])) as session:
        rejudge(session, apply=apply)
    logger.info("rejudge: %s", "applied" if apply else "dry run — pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
