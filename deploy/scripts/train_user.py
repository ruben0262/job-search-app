"""Fit one account's model from its labelled history, and publish it."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.db.models import User
from libs.db.session import session_scope
from services.trainer.train import NotEnoughData, build_training_set, train_user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--artifacts", default=os.environ.get("MODEL_DIR", "/var/lib/jobsearch/models"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2
        try:
            data = build_training_set(session, user.id)
        except NotEnoughData as exc:
            print(f"cannot train: {exc}", file=sys.stderr)
            return 1
        print(f"{args.email}: {len(data.targets)} usable labels at generation "
              f"{user.profile_generation}, embedded by {data.embedder_version}")
        if args.dry_run:
            print("--dry-run: nothing written")
            return 0

        version = train_user(session, user.id, artifact_dir=Path(args.artifacts))
        print(f"trained: model_version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
