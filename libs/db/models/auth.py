"""Session and token storage for `libs.auth`.

Both tables hold **hashes only**. A database dump must not let anyone resume a session or verify an
address — the same property `libs/secrets` gives credentials, applied to the things that grant
access to them.
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

TOKEN_PURPOSES = ("verify_email", "reset_password")


class UserSession(Base):
    """One refresh session. Rotated on every use, so a row is spent rather than long-lived."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # SHA-256 hex of the refresh token. Unique so a replay is a lookup rather than a scan, and so
    # two sessions can never collide on one token.
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)

    expires_at: Mapped[timestamptz]
    # Set when this token was exchanged for its successor. A second presentation of a rotated token
    # is a replay, and `libs.auth.sessions` revokes the whole family on it.
    rotated_at: Mapped[timestamptz | None]
    revoked_at: Mapped[timestamptz | None]

    # Diagnostic only — shown on a "your sessions" page, never used for authorisation. Trusting a
    # user-agent or an IP for access decisions is how a header becomes a credential.
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        # The "log me out everywhere" and "list my sessions" reads. Partial on live rows only:
        # rotated and revoked sessions accumulate forever and are never selected.
        Index("ix_user_sessions_live", "user_id",
              postgresql_where=text("revoked_at IS NULL AND rotated_at IS NULL")),
    )


class AuthToken(Base):
    """A single-use email token. The plaintext exists in the email and nowhere else."""

    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(32))

    expires_at: Mapped[timestamptz]
    used_at: Mapped[timestamptz | None]
    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"purpose IN {TOKEN_PURPOSES}", name="purpose_valid"),
        # Issuing a token revokes the user's earlier unused ones for the same purpose.
        Index("ix_auth_tokens_outstanding", "user_id", "purpose"),
    )
