"""The setup conversation: one thread per user, replayed into every request.

The reference instance runs its setup agents as slash commands in a terminal, where the transcript
is the terminal. A hosted product has no terminal, so the transcript is a table — and it has to be,
because `/setup-prompts` and `/setup-scorer` both assume `/setup` already ran and its answers are
still visible.
"""
from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# The commands a user can run in the chat. Ported from `.claude/commands/`; the first is the one
# that has to run before either of the others will start.
SETUP_AGENTS = ("setup", "setup-prompts", "setup-scorer")


class SetupMessage(Base):
    """One turn of the setup conversation."""

    __tablename__ = "setup_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)

    # Which command was driving. Null for a message sent outside one.
    agent: Mapped[str | None] = mapped_column(String(32))

    # The assistant's tool calls and their results, in the shape the API takes them back. Replayed
    # so the model can see the file it just wrote rather than being told about it.
    tool_calls: Mapped[list | None] = mapped_column(JSONB)

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        Index("ix_setup_messages_user", "user_id", "id"),
        CheckConstraint("role IN ('user', 'assistant')", name="setup_messages_role"),
    )
