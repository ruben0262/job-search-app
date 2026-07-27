"""Score the corpus for one account, through the scorer the platform actually serves with.

    python deploy/scripts/score_user.py --email you@example.com

The same registry and serve path a worker would use — this exists so a migrated account can be
scored once, on demand, before a queue lane is driving it. Rows already scored by the current model
version are skipped, so it is cheap to re-run and safe to interrupt.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.db.models import CorpusJob, User, UserJobState
from libs.db.session import session_scope
from libs.tenant.repository import TenantRepository
from services.scorer.registry import ModelRegistry, ModelUnavailable
from services.scorer.serve import score_rows

BATCH = 500

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--limit", type=int, default=100_000)
    args = parser.parse_args(argv)

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2
        user_id, model_version = user.id, user.model_version
        try:
            ModelRegistry(session).get(user_id)
        except ModelUnavailable as exc:
            print(f"cannot score: {exc.reason}", file=sys.stderr)
            return 1

    scored, bands = 0, {"high": 0, "needs-review": 0, "skip": 0}
    while scored < args.limit:
        with session_scope() as session:
            model = ModelRegistry(session).get(user_id)
            # Rows this model has not seen. `scored_model_version` is what makes a re-run cheap:
            # after a retrain everything is stale again, which is correct — the scores mean
            # something different — and until then nothing is recomputed.
            rows = session.execute(
                select(CorpusJob.id, CorpusJob.title, CorpusJob.description,
                       CorpusJob.embedding, CorpusJob.embedding_dim, CorpusJob.embedding_version)
                .outerjoin(UserJobState,
                           (UserJobState.job_id == CorpusJob.id)
                           & (UserJobState.user_id == user_id))
                .where(CorpusJob.embedding.is_not(None),
                       (UserJobState.scored_model_version.is_(None))
                       | (UserJobState.scored_model_version != model_version))
                .limit(BATCH)).mappings().all()
            if not rows:
                break

            tenant = TenantRepository(session)
            for result in score_rows(model, [dict(r) for r in rows]):
                tenant.record_score(user_id, result.job_id, score=result.score,
                                    model_version=model_version)
                bands[result.decision] += 1
                scored += 1
        print(f"  … {scored} scored", flush=True)

    print(f"\n{args.email}: {scored} scored — "
          + ", ".join(f"{count} {band}" for band, count in bands.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
