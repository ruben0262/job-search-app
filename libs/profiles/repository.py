"""Reading and writing one user's profile files."""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import SCORING_RELEVANT_FILES, UserProfileFile
from libs.profiles import (
    ProfileIncomplete,
    grounding,
    is_filled,
    missing_setup,
    seed_files,
    ships_usable,
)

logger = logging.getLogger(__name__)


class ProfileFiles:
    """One user's markdown profile. **`user_id` on every method, never inferred.**"""

    def __init__(self, session: Session, user_id: _uuid.UUID):
        self._s = session
        self._user = user_id

    def all(self) -> dict[str, str]:
        """`{name: content}` for this user."""
        rows = self._s.execute(
            select(UserProfileFile.name, UserProfileFile.content)
            .where(UserProfileFile.user_id == self._user)).all()
        return dict(rows)

    def read(self, name: str) -> str | None:
        return self._s.scalar(
            select(UserProfileFile.content)
            .where(UserProfileFile.user_id == self._user, UserProfileFile.name == name))

    def listing(self) -> list[dict]:
        """What the setup agent and the file panel both need: names, kinds, filled state."""
        rows = self._s.execute(
            select(UserProfileFile.name, UserProfileFile.kind, UserProfileFile.filled,
                   UserProfileFile.updated_at)
            .where(UserProfileFile.user_id == self._user)
            .order_by(UserProfileFile.name)).all()
        return [{"name": n, "kind": k, "filled": f, "updated_at": u} for n, k, f, u in rows]

    def write(self, name: str, content: str, *, kind: str | None = None) -> bool:
        """Store a file."""
        existing_kind = self._s.scalar(
            select(UserProfileFile.kind)
            .where(UserProfileFile.user_id == self._user, UserProfileFile.name == name))
        resolved = kind or existing_kind
        if resolved is None:
            raise ValueError(f"{name} is new and has no kind — pass one")

        self._s.execute(
            pg_insert(UserProfileFile)
            .values(user_id=self._user, name=name, content=content, kind=resolved,
                    filled=is_filled(content))
            .on_conflict_do_update(
                index_elements=["user_id", "name"],
                set_={"content": content, "kind": resolved, "filled": is_filled(content)}))
        return name in SCORING_RELEVANT_FILES

    def seed(self) -> int:
        """Give a new account the scaffolds."""
        # Counted with RETURNING: for `ON CONFLICT DO NOTHING` the driver reports -1 for `rowcount`.
        written = 0
        for name, kind, content in seed_files():
            inserted = self._s.scalar(
                pg_insert(UserProfileFile)
                .values(user_id=self._user, name=name, kind=kind, content=content,
                        filled=ships_usable(name))
                .on_conflict_do_nothing(index_elements=["user_id", "name"])
                .returning(UserProfileFile.name))
            written += 1 if inserted else 0
        return written

    def grounding(self) -> str:
        """The system prompt for a generation call. Raises `ProfileIncomplete` if setup is unfinished."""
        return grounding(self.all())

    def is_ready(self) -> bool:
        """Whether this account can generate documents at all."""
        try:
            self.grounding()
        except ProfileIncomplete:
            return False
        return True

    def missing_setup(self) -> list[str]:
        """Setup files still absent or unfilled."""
        return missing_setup(self.all())

    def repair_filled_flags(self) -> list[str]:
        """Recompute `filled` from content."""
        repaired = []
        for row in self._s.scalars(
                select(UserProfileFile).where(UserProfileFile.user_id == self._user)):
            correct = is_filled(row.content)
            if row.filled != correct:
                row.filled = correct
                repaired.append(row.name)
        return repaired
