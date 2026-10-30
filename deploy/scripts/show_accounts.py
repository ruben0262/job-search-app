"""What every account actually has."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.scorer.registry import ModelRegistry, ModelUnavailable
from sqlalchemy import func, select

from libs.db.models import User, UserJobState, UserLabel, UserModel
from libs.db.session import session_scope


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with session_scope() as session:
        registry = ModelRegistry(session)
        for user in session.scalars(select(User).order_by(User.created_at)):
            labels = session.scalar(
                select(func.count()).select_from(UserLabel)
                .where(UserLabel.user_id == user.id)) or 0
            scored = session.scalar(
                select(func.count()).select_from(UserJobState)
                .where(UserJobState.user_id == user.id,
                       UserJobState.score.is_not(None))) or 0
            applied = session.scalar(
                select(func.count()).select_from(UserJobState)
                .where(UserJobState.user_id == user.id,
                       UserJobState.applied_at.is_not(None))) or 0
            artifact = session.scalar(
                select(UserModel).where(UserModel.user_id == user.id,
                                        UserModel.is_active.is_(True)))

            print(f"\n{user.email}")
            print(f"  status          {user.status}, active={user.active}, "
                  f"login={'set' if not user.password_hash.startswith('!') else 'NOT SET'}")
            print(f"  profile         generation {user.profile_generation}, "
                  f"version {user.profile_version}, "
                  f"{len((user.profile or {}).get('not_list', []))} hard gates")
            print(f"  labels          {labels} at generation {user.profile_generation}")
            print(f"  model           version {user.model_version}"
                  + (f", {artifact.n_labels} labels, feature schema "
                     f"{artifact.feature_schema_version}" if artifact else " (none)"))
            # The check that matters: would the scorer actually serve this model right now?
            try:
                loaded = registry.get(user.id)
                print(f"  servable        yes — cutoff {loaded.cutoff}, "
                      f"{len(loaded.features.core)} core / {len(loaded.features.offstack)} "
                      f"off-stack tokens")
            except ModelUnavailable as exc:
                print(f"  servable        NO — {exc.reason}")
            print(f"  feed            {scored} scored, {applied} applied, "
                  f"telegram={user.telegram_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
