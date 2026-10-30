"""What every account actually has."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.db import database, find
from libs.db.models import User, UserConfig, UserDocument, UserJobState
from libs.matching import green_words


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    db = database()
    for user in find(db, User, {}, sort=[("created_at", 1)]):
        config = (db[UserConfig.COLLECTION].find_one({"user_id": user.id}) or {}).get("config")
        documents = db[UserDocument.COLLECTION].count_documents({"user_id": user.id})
        states = db[UserJobState.COLLECTION]
        sent = states.count_documents({"user_id": user.id, "notified_at": {"$ne": None}})
        applied = states.count_documents({"user_id": user.id, "applied_at": {"$ne": None}})

        print(f"\n{user.email}")
        print(f"  status          {user.status}, active={user.active}, "
              f"login={'set' if not user.password_hash.startswith('!') else 'NOT SET'}")
        print(f"  words           {len(green_words(config))} green, {documents} document(s)")
        print(f"  feed            {sent} sent, {applied} applied, telegram={user.telegram_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
