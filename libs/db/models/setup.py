"""The setup conversations: one thread per chat, replayed into every request."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# The commands a user can run in the chat.
SETUP_AGENTS = ("setup-director", "setup-context", "setup", "setup-prompts",
                "setup-role-angles", "setup-scorer")

# What a conversation is called before anybody has said anything in it.
DEFAULT_TITLE = "Untitled chat"


class SetupConversation(Base):
    """One chat thread. The unit of context."""

    __tablename__ = "setup_conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Derived from the first thing the user says: a naming call would be one more thing to fail.
    title: Mapped[str] = mapped_column(String(80), default=DEFAULT_TITLE)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    # Ordering key for the list, so the chat you are actually in sits at the top.
    last_message_at: Mapped[timestamptz | None]

    __table_args__ = (
        Index("ix_setup_conversations_user", "user_id", "last_message_at"),
    )


class SetupMessage(Base):
    """One turn of one conversation."""

    __tablename__ = "setup_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The context boundary.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("setup_conversations.id", ondelete="CASCADE"))

    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)

    # How far this turn has got.
    status: Mapped[str] = mapped_column(String(16), default="done", server_default="done")
    # Why it failed, in the words the reader gets. Null unless `status = 'failed'`.
    error: Mapped[str | None] = mapped_column(Text)

    # Which command was driving. Null for a message sent outside one.
    agent: Mapped[str | None] = mapped_column(String(32))

    # The assistant's tool calls and their results, in the shape the API takes them back.
    tool_calls: Mapped[list | None] = mapped_column(JSONB)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        # The only query this table has: one conversation's transcript, oldest first.
        Index("ix_setup_messages_conversation", "conversation_id", "id"),
        Index("ix_setup_messages_user", "user_id", "id"),
        CheckConstraint("role IN ('user', 'assistant')", name="setup_messages_role"),
        # `cancelling` is the user having asked to stop and the worker not having noticed yet.
        CheckConstraint(
            "status IN ('pending', 'streaming', 'done', 'failed', 'cancelling', 'cancelled')",
            name="setup_messages_status"),
    )
