"""Reading and writing one user's profile files.

Separate from the resolver in `__init__` for the same reason `libs/tenant/repository.py` is separate
from the renderers: the assembly rules are pure functions that a test can drive without a database,
and everything that touches a session lives here where `user_id` is required on every call.
"""
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
        """What the setup agent and the file panel both need: names, kinds, filled state.

        Deliberately without `content` — the agent reads a file when it needs one, and shipping
        every byte of eight files into a tool result is how a chat session runs out of context.
        """
        rows = self._s.execute(
            select(UserProfileFile.name, UserProfileFile.kind, UserProfileFile.filled,
                   UserProfileFile.updated_at)
            .where(UserProfileFile.user_id == self._user)
            .order_by(UserProfileFile.name)).all()
        return [{"name": n, "kind": k, "filled": f, "updated_at": u} for n, k, f, u in rows]

    def write(self, name: str, content: str, *, kind: str | None = None) -> bool:
        """Store a file. Returns whether this edit is **scoring-relevant**.

        The return value is the whole retraining policy in one boolean: a `True` means the user's
        labels now answer a slightly different question, and the caller advances the counter that
        eventually asks them whether to retrain. It never invalidates anything here — that is a
        decision with a real cost, and it belongs to the user (`REBUILD_PLAN.md`).

        **Does not commit** — the caller owns the transaction, so a file written by the setup agent
        and the message that announced it land together.
        """
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
        """Give a new account the scaffolds. Existing rows are left alone.

        `DO NOTHING` rather than an upsert on purpose: re-seeding must never overwrite a profile
        somebody has already filled in, and a seed call is exactly the sort of thing that gets run
        twice during a migration.
        """
        # Counted with RETURNING rather than `rowcount`: for `ON CONFLICT DO NOTHING` the driver
        # reports -1 here, so a first seed of eight files announced "-8 scaffolds". A count nobody
        # can trust is worse than no count, and this one is printed to an operator.
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
