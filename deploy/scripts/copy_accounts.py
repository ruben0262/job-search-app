"""Copy the accounts out of the old Postgres database into MongoDB, once (ROADMAP Phase 9).

**Accounts only** (owner, 2026-10-30): people, their logins (password hashes), settings, words,
uploaded documents, profile files, credentials, browsers, proxies and notifications, plus what keeps
the harvest working — the operator's settings and keywords, the company boards, any paused lanes.
Postings are not copied: the harvester collects them again within hours, and applied and hidden marks
go with them. Sessions are not copied either, so everybody signs in once more.

The source must be at the final Postgres schema — Alembic head `3c7d1e9a5b20`, the tag
`model-on-postgres` — so this reads exactly one shape. The target must hold no accounts yet.
Run it with `--dry-run` first: it reads everything, converts it, and writes nothing.

    POSTGRES_URL=postgresql://... MONGODB_URI=... python deploy/scripts/copy_accounts.py --dry-run

Needs `psycopg`, which the platform no longer depends on: `pip install "psycopg[binary]"` for the run.
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.db import database, ensure_schema
from libs.db.models import (
    SECRET_KINDS,
    LanePause,
    OperatorKeyword,
    PlatformSetting,
    PushSubscription,
    SetupConversation,
    SetupMessage,
    User,
    UserConfig,
    UserDocument,
    UserNotification,
    UserProfileFile,
    UserSecret,
)
from libs.db.models.ats import AtsToken
from libs.limits import BY_KEY
from libs.settings import DEFAULTS

FINAL_REVISION = "3c7d1e9a5b20"

# Source table → model, and the SQL that reads it. Columns are named; `SELECT *` would carry the
# switched-off model's columns, which the models refuse.
TABLES: list[tuple[type, str]] = [
    (User, """SELECT id, email, password_hash, status, email_verified_at, full_name, links,
                     processing_consent_at, processing_consent_version, onboarding_completed_at,
                     last_active_at, timezone, active, is_admin, limits,
                     linkedin_next_run_at, telegram_status, telegram_chat_id,
                     profile, documents_changed_at, processed_at, hard_data_changed_at, suggestions,
                     wrapped_dek, created_at, updated_at FROM users"""),
    (UserConfig, "SELECT user_id, config, schema_version, updated_at FROM user_config"),
    (UserSecret, """SELECT user_id, kind, ciphertext, nonce, last_4, validated_at, updated_at
                    FROM user_secrets"""),
    (UserDocument, """SELECT id, user_id, kind, filename, text, text_sha256, chars, uploaded_at
                      FROM user_documents"""),
    (UserProfileFile, """SELECT user_id, name, content, kind, filled, created_at, updated_at
                         FROM user_profile_files"""),
    (PushSubscription, "SELECT endpoint, user_id, p256dh, auth, created_at FROM push_subscriptions"),
    (UserNotification, """SELECT id, user_id, event, level, title, body, url, created_at, read_at
                          FROM user_notifications WHERE event <> 'model.trained'"""),
    # Only the help chat's: the setup agents' conversations went with the switched-off model.
    (SetupConversation, """SELECT DISTINCT c.id, c.user_id, c.title, c.created_at FROM setup_conversations c
                           JOIN setup_messages m ON m.conversation_id = c.id WHERE m.agent = 'support'"""),
    (SetupMessage, """SELECT id, user_id, conversation_id, role, content, status, error, agent, tool_calls,
                             created_at FROM setup_messages WHERE agent = 'support'"""),
    (PlatformSetting, "SELECT key AS id, value, updated_at, updated_by FROM platform_settings"),
    (OperatorKeyword, "SELECT id, keyword, location, note, created_at, created_by FROM operator_keywords"),
    (AtsToken, "SELECT ats, token, first_seen_at, dead_at FROM ats_tokens"),
]


def _plain(value):
    """What Postgres hands back, as MongoDB stores it."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, memoryview):
        return bytes(value)
    return value


def converted(model: type, row: dict) -> dict | None:
    """One row as the model's document, or None when it is not carried over. Every document passes
    through the model, so a column the model does not declare fails here rather than in a page."""
    row = {name: _plain(value) for name, value in row.items()}
    if model is User:
        # Limits the catalogue no longer has (the labeller's `label_pool`) govern nothing.
        row["limits"] = {key: value for key, value in (row["limits"] or {}).items() if key in BY_KEY}
    if model is UserSecret and (row["kind"] not in SECRET_KINDS or row["kind"] == "telegram_bot"):
        # An old personal bot's token, with no bot name and a chat Phase 8 reset: re-added in Settings
        # in a minute (the Telegram steps there), rather than half-carried over.
        return None
    if model is PlatformSetting and row["id"] not in DEFAULTS:
        return None  # a setting for something that no longer exists
    if model is SetupMessage and row["status"] in ("pending", "streaming", "cancelling"):
        row["status"] = "failed"  # nothing will finish it: the process writing it is gone
    if "id" in row:
        row["_id"] = row.pop("id")
    return model.from_doc(row).to_doc()


def copy(source, db, *, write: bool) -> dict[str, int]:
    """Every table in `TABLES`, then the integer counters moved past the highest id copied."""
    counts: dict[str, int] = {}
    for model, sql in TABLES:
        with source.cursor() as cursor:
            cursor.execute(sql)
            docs = [doc for doc in (converted(model, row) for row in cursor) if doc is not None]
        counts[model.COLLECTION] = len(docs)
        if write and docs:
            db[model.COLLECTION].insert_many(docs)
    with source.cursor() as cursor:
        cursor.execute("SELECT to_regclass('lane_pauses') IS NOT NULL AS present")
        if cursor.fetchone()["present"]:
            cursor.execute("SELECT lane AS id, reason, paused_at FROM lane_pauses")
            pauses = [LanePause(id=row["id"], reason=row["reason"], paused_at=row["paused_at"]).to_doc()
                      for row in cursor]
            counts[LanePause.COLLECTION] = len(pauses)
            if write and pauses:
                db[LanePause.COLLECTION].insert_many(pauses)
    if write:
        for model in (UserDocument, UserNotification, SetupMessage, OperatorKeyword):
            highest = db[model.COLLECTION].find_one({}, {"_id": 1}, sort=[("_id", -1)])
            if highest is not None:
                db.counters.update_one({"_id": model.COLLECTION}, {"$max": {"value": highest["_id"]}},
                                       upsert=True)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="read and convert everything, write nothing")
    args = parser.parse_args(argv)

    import psycopg
    from psycopg.rows import dict_row

    url = os.environ.get("POSTGRES_URL", "").replace("postgresql+psycopg://", "postgresql://")
    if not url:
        print("POSTGRES_URL is not set — the old database to copy from", file=sys.stderr)
        return 2
    with psycopg.connect(url, row_factory=dict_row) as source:
        with source.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()["version_num"]
        if revision != FINAL_REVISION:
            print(f"REFUSING: the source is at Alembic revision {revision}, not {FINAL_REVISION}. Upgrade "
                  "it first from the tag: git checkout model-on-postgres && alembic upgrade head",
                  file=sys.stderr)
            return 2
        db = database()
        if not args.dry_run:
            if db[User.COLLECTION].estimated_document_count():
                print(f"REFUSING: {db.name} already holds accounts — copy into an empty database",
                      file=sys.stderr)
                return 2
            ensure_schema(db)
        counts = copy(source, db, write=not args.dry_run)
    print(("would copy " if args.dry_run else "copied ")
          + ", ".join(f"{n} {name}" for name, n in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
