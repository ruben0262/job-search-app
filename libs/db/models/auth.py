"""Session and token storage for `libs.auth`."""
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz

# `telegram_connect` rides in a t.me link rather than an email: it ties a Telegram chat to the account.
TOKEN_PURPOSES = ("verify_email", "reset_password", "telegram_connect")


class UserSession(Base):
    """One refresh session. Rotated on every use, so a row is spent rather than long-lived."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # SHA-256 hex of the refresh token.
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)

    expires_at: Mapped[timestamptz]
    # Set when this token was exchanged for its successor.
    rotated_at: Mapped[timestamptz | None]
    revoked_at: Mapped[timestamptz | None]

    # Diagnostic only — shown on a "your sessions" page, never used for authorisation.
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        # The "log me out everywhere" and "list my sessions" reads.
        Index("ix_user_sessions_live", "user_id",
              postgresql_where=text("revoked_at IS NULL AND rotated_at IS NULL")),
    )


class AuthToken(Base):
    """A single-use token. The plaintext exists in the link it travels in and nowhere else."""

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


# What a send was for. Not who it was to — see `libs.mail.quota`.
MAIL_PURPOSES = ("verify_email", "reset_password", "notice")


class MailSend(Base):
    """One outbound message, counted against the platform's daily allowance."""

    __tablename__ = "mail_sends"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    purpose: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"purpose IN {MAIL_PURPOSES}", name="purpose_valid"),
        # The only read: how many in the last 24 hours.
        Index("ix_mail_sends_recent", "sent_at"),
    )
