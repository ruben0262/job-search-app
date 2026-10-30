"""The help chat's conversations and messages. A reply is written into its message as it streams."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

DEFAULT_TITLE = "Untitled chat"
# `cancelling` is the person having asked to stop and the writer not having noticed yet.
MESSAGE_STATUSES = ("pending", "streaming", "done", "failed", "cancelling", "cancelled")


@dataclass(kw_only=True)
class SetupConversation(Document):
    COLLECTION = "setup_conversations"
    INDEXES = (IndexModel([("user_id", 1), ("created_at", 1)], name="by_user"),)

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID
    title: str = DEFAULT_TITLE
    created_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class SetupMessage(Document):
    COLLECTION = "setup_messages"
    SEQUENCE = True
    CHOICES = {"role": ("user", "assistant"), "status": MESSAGE_STATUSES}  # noqa: RUF012
    INDEXES = (IndexModel([("conversation_id", 1), ("_id", 1)], name="by_conversation"),
               IndexModel([("user_id", 1), ("_id", 1)], name="by_user"),
               IndexModel("status", partialFilterExpression={"status": {"$in": ["pending", "streaming"]}},
                          name="unfinished"))

    id: int | None = None
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    status: str = "done"
    error: str | None = None
    agent: str | None = None
    tool_calls: list | None = None
    # Who is writing this reply, and until when: a worker that dies lets go when its lease runs out.
    lease_until: datetime | None = None
    created_at: datetime = field(default_factory=now)
