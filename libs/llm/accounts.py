"""The AI accounts a person links in Settings, and the one their CVs and cover letters are written with
(owner, 2026-11-09).

An account is a sign-in to their own Claude, ChatGPT (Codex) or Google (Gemini) account, kept as the
files that account's own command-line tool writes (`libs.signin`, `libs.llm.cli`), or an API key. A
person may link several of each; one is in use at a time, and switching one on switches the rest off.
Without one in use, no document is made. Each is sealed like every credential (`libs.secrets`) and never
shown back: Settings shows its label and when it was linked, and nothing of the secret.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database

from libs.db.models import UserSecret
from libs.db.models.user import AI_KINDS, KEY_KINDS, LOGIN_KINDS

logger = logging.getLogger(__name__)

# Whose models each kind runs, for `libs.settings.document_model`.
PROVIDER = {"claude_login": "anthropic", "claude_api": "anthropic", "codex_login": "codex",
            "openai_api": "openai", "gemini_login": "gemini", "gemini_api": "gemini"}
# What a kind is called in Settings, before the account says more about itself.
KIND_NAMES = {"claude_login": "Claude", "codex_login": "ChatGPT", "gemini_login": "Google Gemini",
         "claude_api": "Claude API key", "openai_api": "OpenAI API key", "gemini_api": "Gemini API key"}
# In this order the first of a person's accounts is put in use, when none is.
PREFERRED = (*LOGIN_KINDS, *KEY_KINDS)


@dataclass(frozen=True)
class Account:
    """What Settings shows of one linked account: never anything of its secret."""

    id: str
    kind: str
    label: str
    in_use: bool
    created_at: datetime


def listing(db: Database, user_id: _uuid.UUID) -> list[Account]:
    """Their AI accounts, newest first. Switching one on moves its switch, not its row: a list that
    put the one in use on top answered every switch with the same top row, as if nothing changed."""
    rows = db[UserSecret.COLLECTION].find(
        {"user_id": user_id, "kind": {"$in": list(AI_KINDS)}},
        {"kind": 1, "label": 1, "active": 1, "created_at": 1, "updated_at": 1})
    accounts = [Account(id=str(row["_id"]), kind=row["kind"],
                        label=row.get("label") or KIND_NAMES[row["kind"]], in_use=bool(row.get("active")),
                        created_at=row.get("created_at") or row["updated_at"])
                for row in rows]
    return sorted(accounts, key=lambda each: each.created_at, reverse=True)


def in_use(db: Database, user_id: _uuid.UUID) -> UserSecret | None:
    """The account their documents are written with, or None."""
    return UserSecret.from_doc(db[UserSecret.COLLECTION].find_one({"user_id": user_id, "active": True}))


def use(db: Database, user_id: _uuid.UUID, account_id: str) -> bool:
    """Put this account in use and every other one of theirs out of it. False if it is not theirs."""
    key = _key(account_id)
    secrets = db[UserSecret.COLLECTION]
    if key is None or not secrets.count_documents({"_id": key, "user_id": user_id,
                                                   "kind": {"$in": list(AI_KINDS)}}, limit=1):
        return False
    # Off first: one account in use is a unique index, which a second one on at once would break.
    secrets.update_many({"user_id": user_id, "active": True, "_id": {"$ne": key}},
                        {"$set": {"active": False}})
    secrets.update_one({"_id": key}, {"$set": {"active": True}})
    return True


def stop(db: Database, user_id: _uuid.UUID, account_id: str) -> bool:
    """Take this account out of use, leaving none in use. False if it is not theirs."""
    key = _key(account_id)
    return key is not None and bool(db[UserSecret.COLLECTION].update_one(
        {"_id": key, "user_id": user_id, "kind": {"$in": list(AI_KINDS)}},
        {"$set": {"active": False}}).matched_count)


def unlink(db: Database, user_id: _uuid.UUID, account_id: str) -> bool:
    """Forget this account and its secret. False if it is not theirs."""
    key = _key(account_id)
    return key is not None and bool(db[UserSecret.COLLECTION].delete_one(
        {"_id": key, "user_id": user_id, "kind": {"$in": list(AI_KINDS)}}).deleted_count)


def settle(db: Database) -> dict[str, int]:
    """What the deploy runs after the schema (`python -m libs.db`), on accounts linked before
    2026-11-09: the keys get a label and one of each person's goes in use, and the Claude tokens from
    `claude setup-token` are deleted (owner: those sign in again, in full). Running it again does nothing."""
    secrets = db[UserSecret.COLLECTION]
    removed = secrets.delete_many({"kind": "claude_oauth"}).deleted_count
    labelled = 0
    for kind in AI_KINDS:
        labelled += secrets.update_many({"kind": kind, "label": None},
                                        {"$set": {"label": KIND_NAMES[kind]}}).modified_count
    switched = 0
    for user_id in secrets.distinct("user_id", {"kind": {"$in": list(AI_KINDS)}}):
        if secrets.count_documents({"user_id": user_id, "active": True}, limit=1):
            continue
        rows = list(secrets.find({"user_id": user_id, "kind": {"$in": list(AI_KINDS)}}, {"kind": 1}))
        first = min(rows, key=lambda row: PREFERRED.index(row["kind"]))
        secrets.update_one({"_id": first["_id"]}, {"$set": {"active": True}})
        switched += 1
    if removed or labelled or switched:
        logger.info("accounts: settled %d label(s), %d put in use, %d setup-token(s) deleted",
                    labelled, switched, removed)
    return {"labelled": labelled, "switched": switched, "removed": removed}


def _key(account_id: str) -> ObjectId | None:
    try:
        return ObjectId(account_id)
    except (InvalidId, TypeError):
        return None
