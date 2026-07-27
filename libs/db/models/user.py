"""The tenant: account, scheduling state, config, and credentials.

``users`` is the row every other per-user table cascades from. Deleting it deletes the account
completely (PLATFORM_ARCHITECTURE.md §8) — which is only true as long as nobody ever denormalises
user data onto a corpus row.

**Why some config lives in columns and the rest in JSONB.** The scheduler's dispatcher runs every
60 s over every active user (SCHEDULER_PLAN.md §6):

    SELECT id FROM users WHERE active AND next_run_at <= now()

Fields that loop touches — ``active``, ``next_run_at``, ``notify_interval_min``, ``timezone`` — are
real columns with a real index. Everything else the config form collects (keywords, locations,
seniority, salary floor …) is JSONB on ``user_config``, because it is read once per harvest match
pass and never in a hot predicate. The split is by access pattern, not by importance, and the form in
TENANT_SAFETY.md §4 writes across both without caring.

``model_version``, ``profile_version`` and ``profile_generation`` are the three counters that keep
scoring honest, and they live here because the scorer reads them on every serve:

- ``model_version`` — bumped by the trainer on completion. The scorer compares before serving and
  reloads on change, which is what removes today's "recreate the container to pick up a retrain"
  step (PLATFORM_ARCHITECTURE.md §7).
- ``profile_version`` — hash of the user's rendered rubric. Scopes which labels are still valid.
- ``profile_generation`` — the single counter that keeps labels, feature extractor, and model
  artifact in step. Editing a profile bumps it; anything built against an older generation is stale
  by definition rather than by someone remembering (PROFILE_CAPTURE_PLAN.md §6.4).
"""
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

# Account lifecycle (GAPS_REVIEW G11, TENANT_SAFETY.md §5). `pending_verification` until the email is
# confirmed — nothing before that point may touch a paid resource or consume harvest budget.
USER_STATUSES = ("pending_verification", "active", "suspended", "deleted")

# G6: the likeliest real-world delivery failure is the user blocking the bot or revoking the token.
# Telegram answers 403 and retrying is pointless, so it becomes a terminal state that stops enqueuing
# and emails the user, rather than five wasted attempts followed by silence (SCHEDULER_PLAN.md §4).
TELEGRAM_STATUSES = ("unconfigured", "connected", "broken")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Generic failure messages on login mean this is never used to confirm an address exists
    # (TENANT_SAFETY.md §5). Lowercased by the application before write; a functional unique index
    # would be the alternative, but normalising once at the boundary is easier to reason about.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending_verification")
    email_verified_at: Mapped[timestamptz | None]

    # IANA name, validated against the tz database (G7). Not an offset: offsets do not survive DST,
    # and "resets at midnight your time" has to keep meaning the same thing in November.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    # --- scheduling: read by the dispatcher every 60 s ---
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_interval_min: Mapped[int] = mapped_column(Integer, default=60)
    next_run_at: Mapped[timestamptz | None]
    # The per-user LinkedIn loop runs on its own interval, deliberately slower than the 15-minute
    # shared harvest: a user on a 4-hour digest does not need LinkedIn scraped 96 times a day, and
    # they are paying for those bytes themselves (HARVESTER_PLAN.md §5.3).
    linkedin_interval_min: Mapped[int] = mapped_column(Integer, default=240)
    linkedin_next_run_at: Mapped[timestamptz | None]

    telegram_status: Mapped[str] = mapped_column(String(32), default="unconfigured")
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))

    # --- the three counters that keep scoring honest (see module docstring) ---
    model_version: Mapped[int] = mapped_column(Integer, default=0)
    profile_version: Mapped[str | None] = mapped_column(String(64))
    profile_generation: Mapped[int] = mapped_column(Integer, default=0)

    # Per-user DEK, wrapped by the KEK. The KEK itself lives in the environment and must never reach
    # the database or its backups — that separation is the entire reason a stolen dump is useless,
    # and it is the thing most often implemented and then quietly defeated by backing up the env file
    # next to the database (TENANT_SAFETY.md §2, OPERATIONS_PLAN.md §7).
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
        # Bounds from TENANT_SAFETY.md §4. Enforced here as well as in the Pydantic schema on
        # purpose: the form is the usability layer, the database is the authority. A 1-minute
        # interval is a self-inflicted Telegram rate-limit ban.
        CheckConstraint("notify_interval_min BETWEEN 15 AND 1440", name="notify_interval_bounds"),
        CheckConstraint("linkedin_interval_min BETWEEN 15 AND 1440",
                        name="linkedin_interval_bounds"),
        # The dispatcher's predicate, indexed. Partial, because rows that are not active are never
        # selected and there is no reason to carry them in the index.
        Index("ix_users_due", "next_run_at", postgresql_where=text("active")),
        Index("ix_users_linkedin_due", "linkedin_next_run_at", postgresql_where=text("active")),
    )


class UserConfig(Base):
    """The config form's contents, as JSONB. One row per user.

    JSONB rather than columns because this is exactly the "flexible document" case, and it is read
    once per harvest match pass rather than in a hot predicate. The shape is **not** free-form: it is
    validated server-side against the same Pydantic model the React form renders from
    (``libs.contracts.config``), exported as JSON Schema so the two cannot drift
    (TENANT_SAFETY.md §4, CODE_STRUCTURE.md §8).

    ``schema_version`` is what makes a config-shape change survivable: old rows keep their version,
    and a migration reads it to decide how to upgrade them. Without it, changing the shape means
    guessing what any given row means.
    """

    __tablename__ = "user_config"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB)
    schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="config")


# One row per credential kind. Separate rows rather than columns on `users` so that rotating or
# revoking one credential touches one row, and so adding a kind is an INSERT rather than a migration.
SECRET_KINDS = ("claude_api", "claude_oauth", "telegram_bot", "proxy")


class UserSecret(Base):
    """A single encrypted credential belonging to one user.

    Envelope encryption (TENANT_SAFETY.md §2): the ciphertext here is sealed with the user's DEK, and
    the DEK is sealed with the KEK on ``users.wrapped_dek``. Per-user DEK so one compromised secret
    cannot decrypt the table and so a single user can be rotated.

    Three handling rules this table's shape enforces:

    - **Write-only through the API.** ``last_4`` exists so the UI can show *something* without the
      value ever being readable back. There is deliberately no path that returns ``ciphertext``.
    - **Rotation replaces, never versions.** ``PUT`` overwrites; the old ciphertext is gone. Keeping
      history here would mean keeping a revoked credential decryptable.
    - **Validated at save.** ``validated_at`` records that a real minimal API call succeeded, so a
      bad key fails at the form rather than silently at 3 a.m. mid-backfill.

    Decryption happens only inside the worker making the call, in memory, for the duration of that
    call — never on ``api`` unless ``api`` is the caller.
    """

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
