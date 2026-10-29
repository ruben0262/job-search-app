"""The tenant: account, scheduling state, config, and credentials."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.db.base import Base, timestamptz

# Account lifecycle (GAPS_REVIEW G11, TENANT_SAFETY.md §5).
#
# `dormant` was removed 2026-08-11. It was a state one function wrote, nothing called, and the login
# path un-set incompletely — `status` went back to 'active' while `active` stayed False, which is a
# silently-harvesting-nothing account. An operator reads `last_active_at` in the accounts list and
# deletes the account if they want it gone; that needs no state of its own (ROADMAP 6c).
USER_STATUSES = ("pending_verification", "active", "suspended", "deleted")

# G6: the likeliest real-world delivery failure is the user blocking the bot or revoking the token.
TELEGRAM_STATUSES = ("unconfigured", "connected", "broken")

# The keys `telegram_routes` may carry (`services/notifier/routing.py` is the reader).
TELEGRAM_ROUTE_KEYS = ("hu", "linkedin", "other")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Generic failure messages on login mean this never confirms an address exists.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending_verification")
    email_verified_at: Mapped[timestamptz | None]

    # Not derived from the email.
    full_name: Mapped[str | None] = mapped_column(String(120))
    # (url, platform) pairs.
    links: Mapped[list | None] = mapped_column(JSONB)

    # Consent to send document text to Anthropic, and which disclosure version was agreed to (`libs.legal`).
    processing_consent_at: Mapped[timestamptz | None]
    processing_consent_version: Mapped[str | None] = mapped_column(String(16))

    # Set once every onboarding page is satisfied (`ONBOARDING_AND_PROCESSING.md` §2).
    onboarding_completed_at: Mapped[timestamptz | None]

    # Engagement, not login: a Telegram tap counts as much as a sign-in, because somebody reading
    # their cards is using the product. Reported in the admin accounts list, and read by a person
    # deciding whether to delete the account — nothing acts on it automatically.
    last_active_at: Mapped[timestamptz | None]

    # The plan whose limits apply, and per-user grants layered over them.
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), default="free",
                                         server_default="free")

    # IANA name, validated against the tz database (G7).
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    # --- scheduling: read by the dispatcher every 60 s ---
    active: Mapped[bool] = mapped_column(Boolean, default=False)

    # Operator access, grantable from the admin panel.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Whether this user may read the profile files written about them.
    profile_files_visible: Mapped[bool] = mapped_column(Boolean, default=False,
                                                        server_default="false")

    # Set by Stop, read by the labeller before it re-arms.
    labelling_paused: Mapped[bool] = mapped_column(Boolean, default=False,
                                                   server_default="false")

    # Per-account ceilings, set by an operator (`libs.limits`).
    limits: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # The per-user LinkedIn loop runs on its own interval, deliberately slower than the shared harvest.
    linkedin_interval_min: Mapped[int] = mapped_column(Integer, default=240)
    linkedin_next_run_at: Mapped[timestamptz | None]

    telegram_status: Mapped[str] = mapped_column(String(32), default="unconfigured")
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    # Optional per-category channels (`services/notifier/routing.py`).
    telegram_routes: Mapped[dict | None] = mapped_column(JSONB)
    # The path Telegram posts this user's button taps to.
    telegram_webhook_secret: Mapped[str | None] = mapped_column(String(64))

    # --- the three counters that keep scoring honest ---
    model_version: Mapped[int] = mapped_column(Integer, default=0)
    profile_version: Mapped[str | None] = mapped_column(String(64))
    profile_generation: Mapped[int] = mapped_column(Integer, default=0)

    # The confirmed capture output, and the source of truth for every rendered artifact.
    profile: Mapped[dict | None] = mapped_column(JSONB)
    profile_schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)

    # The unconfirmed output of *process data*, waiting for the user to read it.
    profile_draft: Mapped[dict | None] = mapped_column(JSONB)
    # `capture` or `manual`.
    capture_source: Mapped[str | None] = mapped_column(String(16))

    # The two the dashboard compares to decide whether the account needs processing.
    documents_changed_at: Mapped[timestamptz | None]
    processed_at: Mapped[timestamptz | None]

    # When the user last changed something they typed themselves — their name, their links, their
    # search settings. Compared against each profile file's own `updated_at` to find the ones written
    # before it (`libs.profiles.stale_after`).
    #
    # **A timestamp rather than a flag, deliberately.** A "needs refresh" boolean has to be cleared by
    # whatever does the refresh, and a clear that never happens is a permanent banner — or, worse, one
    # cleared too early leaves the files stale with nothing saying so. A comparison needs no clearing:
    # rewriting the file moves its `updated_at` past this and the staleness ends by itself.
    hard_data_changed_at: Mapped[timestamptz | None]

    # The green and red words suggested to them, each with its reason, and the ones they dismissed:
    # `{"green": [...], "red": [...], "dismissed": [...]}`. Recomputed by `libs.suggestions` whenever
    # a document or their search changes; NULL until first worked out.
    suggestions: Mapped[dict | None] = mapped_column(JSONB)

    # The rendered rubric.
    rubric_md: Mapped[str | None] = mapped_column(Text)

    # The generation-grounding block (PROFILE_CAPTURE_PLAN.md §6.2).


    # Per-user DEK, wrapped by the KEK.
    wrapped_dek: Mapped[bytes | None]

    created_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())

    config: Mapped[UserConfig | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False)
    secrets: Mapped[list[UserSecret]] = relationship(
        back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(f"status IN {USER_STATUSES}", name="status_valid"),
        CheckConstraint(f"telegram_status IN {TELEGRAM_STATUSES}", name="telegram_status_valid"),
        # Bounds from TENANT_SAFETY.md §4.
        CheckConstraint("linkedin_interval_min BETWEEN 15 AND 1440",
                        name="linkedin_interval_bounds"),
        # The LinkedIn tier's own due-list, indexed.
        Index("ix_users_linkedin_due", "linkedin_next_run_at", postgresql_where=text("active")),
        # The webhook path is the identity of the account, so a shared one misattributes button taps.
        Index("uq_users_webhook_secret", "telegram_webhook_secret", unique=True,
              postgresql_where=text("telegram_webhook_secret IS NOT NULL")),
    )


class UserConfig(Base):
    """The config form's contents, as JSONB."""

    __tablename__ = "user_config"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB)
    schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="config")


# One row per credential kind. `gemini_api` and `openai_api` are the person's own keys for AI-written
# documents, beside the two Claude ones (`libs.llm.payer`).
SECRET_KINDS = ("claude_api", "claude_oauth", "telegram_bot", "proxy", "gemini_api", "openai_api")


class UserSecret(Base):
    """A single encrypted credential belonging to one user."""

    __tablename__ = "user_secrets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)

    ciphertext: Mapped[bytes]
    nonce: Mapped[bytes]
    last_4: Mapped[str | None] = mapped_column(String(4))
    validated_at: Mapped[timestamptz | None]
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="secrets")

    __table_args__ = (
        CheckConstraint(f"kind IN {SECRET_KINDS}", name="kind_valid"),
    )
