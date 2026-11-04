"""Reading and writing one user's profile files."""
from __future__ import annotations

import logging
import uuid as _uuid

from pymongo import UpdateOne
from pymongo.database import Database

from libs.db import now
from libs.db.models import User, UserProfileFile
from libs.profiles import (
    grounding,
    is_filled,
    seed_files,
    ships_usable,
    stale_after,
)

logger = logging.getLogger(__name__)


class ProfileFiles:
    """One user's markdown profile. **`user_id` on every method, never inferred.**"""

    def __init__(self, db: Database, user_id: _uuid.UUID):
        self._db = db
        self._files = db[UserProfileFile.COLLECTION]
        self._user = user_id

    def all(self) -> dict[str, str]:
        """`{name: content}` for this user."""
        return {row["name"]: row["content"]
                for row in self._files.find({"user_id": self._user}, {"name": 1, "content": 1})}

    def read(self, name: str) -> str | None:
        row = self._files.find_one({"user_id": self._user, "name": name}, {"content": 1})
        return row["content"] if row else None

    def write(self, name: str, content: str, *, kind: str | None = None) -> None:
        """Store a file."""
        existing = self._files.find_one({"user_id": self._user, "name": name}, {"kind": 1})
        resolved = kind or (existing and existing["kind"])
        if resolved is None:
            raise ValueError(f"{name} is new and has no kind — pass one")
        self._files.update_one(
            {"user_id": self._user, "name": name},
            {"$set": {"content": content, "kind": resolved, "filled": is_filled(content),
                      "updated_at": now()},
             "$setOnInsert": {"created_at": now()}},
            upsert=True)

    def seed(self) -> int:
        """Give a new account the scaffolds."""
        # Only where the name is absent: a file the person already has is theirs, never replaced.
        seeds = [UpdateOne({"user_id": self._user, "name": name},
                           {"$setOnInsert": {"kind": kind, "content": content, "filled": ships_usable(name),
                                             "created_at": now(), "updated_at": now()}}, upsert=True)
                 for name, kind, content in seed_files()]
        return self._files.bulk_write(seeds, ordered=False).upserted_count if seeds else 0

    def grounding(self) -> str:
        """The system prompt for a generation call. Raises `ProfileIncomplete` if setup is unfinished."""
        return grounding(self.all())

    def is_ready(self) -> bool:
        """Whether this account can generate documents at all.

        Reads the one file the answer depends on. `grounding` refuses exactly when `context.md` is
        absent or unfilled, so this asks that question directly rather than loading the account's
        whole profile — ~180,000 characters out of Postgres, inside the webhook a person is waiting
        on after tapping Build, to compute one boolean.

        Deliberately not the `filled` column: it is derived, it has drifted in production before
        (`repair_filled_flags` exists because of it), and a stale flag here would refuse
        a build for somebody whose profile is complete.
        """
        context = self.read("context.md")
        return context is not None and is_filled(context)

    def build_blocker(self) -> str | None:
        """Why this account cannot build a CV or cover letter, or `None` if it can.

        **One question, asked in one place.** It used to be asked twice with different answers:
        the Telegram handler checked `is_ready()` (are the profile files filled?) and the docgen
        worker checked `user.profile` (is there a confirmed profile schema?). An account that
        finished the setup chat but never confirmed the onboarding form passed the first and
        failed the second, so tapping Build answered "it will arrive here shortly" and nothing
        ever arrived. That is not a rare state — it is every user between finishing setup and
        confirming their details.

        **The `user.profile` half of that question is now gone, because the build stopped asking
        it.** A confirmed capture schema was required here and in `docgen._load`, and by the time
        the schema came out of generation the only thing left reading it was `_filename` — a build
        was gated on a confirmed profile in order to name a PDF, which `users.full_name` already
        answers. Guided setup writes the ten profile files and never writes `users.profile`, so
        the gate refused every account that onboarded through the chat: the normal path.

        The refusal text is returned rather than a boolean because the caller shows it to the
        person, and the reason is the useful half.
        """
        if not self.is_ready():
            return ("Finish your setup chat first — a CV needs your profile to build from.")
        if self._db[User.COLLECTION].count_documents({"_id": self._user}, limit=1) == 0:
            return f"no such user: {self._user}"
        return None

    def stale_setup(self) -> list[str]:
        """Setup files written before the user last changed what they typed.

        Answered from each row's own `updated_at`, so nothing extra is stored to know it.
        """
        user = self._db[User.COLLECTION].find_one({"_id": self._user}, {"hard_data_changed_at": 1}) or {}
        written = {row["name"]: row["updated_at"]
                   for row in self._files.find({"user_id": self._user}, {"name": 1, "updated_at": 1})}
        return stale_after(written, user.get("hard_data_changed_at"))

    def repair_filled_flags(self) -> list[str]:
        """Recompute `filled` from content."""
        repaired = []
        for row in self._files.find({"user_id": self._user}, {"name": 1, "content": 1, "filled": 1}):
            correct = is_filled(row["content"])
            if row["filled"] != correct:
                self._files.update_one({"_id": row["_id"]}, {"$set": {"filled": correct}})
                repaired.append(row["name"])
        return repaired
