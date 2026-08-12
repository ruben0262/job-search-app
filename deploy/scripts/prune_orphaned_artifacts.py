"""Model artifacts on disk that no `models` row points at.

**Why this exists as a script rather than a pass in the trainer.** The trainer prunes the versions it
supersedes (`services/trainer/train.py::_prune_artifacts`), and that covers every artifact belonging
to a live account. It cannot reach these: `models.user_id` is `ON DELETE CASCADE`, so deleting an
account removes its rows and leaves its files, and there is then no row left to prune from. Measured
on the box 2026-08-10: **24 MB belonging to an account that no longer exists**, out of 34 MB total.

That is a one-off shape, not a recurring one, so it gets a one-off tool an operator runs and reads —
not a timer. Dry run by default: it prints what it would delete and deletes nothing without
`--delete`.

    python deploy/scripts/prune_orphaned_artifacts.py                    # report only
    python deploy/scripts/prune_orphaned_artifacts.py --delete           # actually remove them
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from libs.db.models import UserModel


def _megabytes(paths: list[Path]) -> float:
    return sum(p.stat().st_size for p in paths) / 1_048_576


def orphans(session: Session, artifact_dir: Path) -> list[Path]:
    """Files in `artifact_dir` that no row claims.

    **Compared by filename, and comparing resolved paths instead was very nearly a disaster.**
    `models.path` holds the path as the *trainer's container* saw it —
    `/var/lib/jobsearch/models/<id>.vN.joblib` — while this runs on the host, where the same files
    live under `MODEL_DIR`. Resolved absolute paths therefore never matched, every file looked
    unclaimed, and a first run reported all eight as orphans including both **active** models. With
    `--delete` that would have removed the artifacts the scorer serves from, for every account.

    The filename is the durable identity — `_write_artifact` composes it from the user id and the
    version, and `resolve_artifact` already falls back to `MODEL_DIR` plus the basename for exactly
    this reason. Only the dry-run default stood between the bug and the damage, which is the argument
    for that default rather than a note about it.
    """
    claimed = {Path(p).name for p in session.scalars(select(UserModel.path))}
    return sorted(p for p in artifact_dir.glob("*.joblib") if p.name not in claimed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts",
                        default=os.environ.get("MODEL_DIR", "/var/lib/jobsearch/models"))
    parser.add_argument("--delete", action="store_true",
                        help="remove the files; without this nothing is written")
    args = parser.parse_args()

    artifact_dir = Path(args.artifacts)
    if not artifact_dir.is_dir():
        print(f"no artifact directory at {artifact_dir}", file=sys.stderr)
        return 2

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    engine = create_engine(url)
    with Session(engine) as session:
        stale = orphans(session, artifact_dir)

    if not stale:
        print(f"{artifact_dir}: no orphaned artifacts")
        return 0

    print(f"{len(stale)} orphaned artifact(s), {_megabytes(stale):.1f} MB:")
    for path in stale:
        print(f"  {path.name}  {path.stat().st_size / 1_048_576:.1f} MB")

    if not args.delete:
        print("\nnothing deleted — re-run with --delete to remove them")
        return 1

    freed = _megabytes(stale)
    for path in stale:
        path.unlink()
    print(f"\ndeleted {len(stale)} file(s), {freed:.1f} MB freed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
