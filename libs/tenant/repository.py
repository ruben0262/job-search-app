"""Per-user reads and writes: the config form, credential metadata, and the LinkedIn gate."""
from __future__ import annotations

import uuid as _uuid

from pymongo.database import Database

from libs.contracts.config import CONFIG_SCHEMA_VERSION
from libs.db import now
from libs.db.models import User, UserConfig


class TenantRepository:
    """All per-user reads and writes."""

    def __init__(self, db: Database):
        self._db = db

    # ── config ───────────────────────────────────────────────────────────────────────────────

    def get_config(self, user_id: _uuid.UUID) -> dict | None:
        """This user's config, or `None` if they have not completed one yet."""
        row = self._db[UserConfig.COLLECTION].find_one({"user_id": user_id}, {"config": 1})
        return row["config"] if row else None

    def save_config(self, user_id: _uuid.UUID, config: dict, *,
                    schema_version: int = CONFIG_SCHEMA_VERSION) -> None:
        """Replace this user's config."""
        self._db[UserConfig.COLLECTION].update_one(
            {"user_id": user_id},
            {"$set": {"config": config, "schema_version": schema_version, "updated_at": now()}},
            upsert=True)

    def active_configs(self) -> dict[_uuid.UUID, dict]:
        """Every active user's config, for the harvester's query-set fold."""
        active = self._db[User.COLLECTION].distinct("_id", {"active": True, "status": "active"})
        return {row["user_id"]: row["config"] for row in self._db[UserConfig.COLLECTION].find(
            {"user_id": {"$in": active}}, {"user_id": 1, "config": 1})}


