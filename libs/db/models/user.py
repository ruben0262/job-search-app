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
#
# `dormant` is what stops an abandoned account scraping LinkedIn on a paid proxy indefinitely. It is
# reversible on any activity and is NOT a punishment: only `suspended` is.
USER_STATUSES = ("pending_verification", "active", "dormant", "suspended", "deleted")

# No engagement for this long → dormant. Long enough to cover a holiday or a hiring pause, short
# enough that an abandoned account is not consuming harvest budget and someone's proxy bandwidth for
# months.
DORMANT_AFTER_DAYS = 30

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

    # **Not derived from the email.** This is what appears at the top of a generated CV, and
    # `r.lekli@…` is not a name (`libs.contracts.onboarding`). Null until onboarding page 1.
    full_name: Mapped[str | None] = mapped_column(String(120))
    # (url, platform) pairs. JSONB rather than a table: read whole, written whole, never queried by
    # element — a join table would buy nothing and cost a migration.
    links: Mapped[list | None] = mapped_column(JSONB)

    # Consent to send document text to Anthropic, and which disclosure version was agreed to
    # (`libs.legal`). Recorded at *process data* rather than at upload: files no longer leave this
    # server, so nothing crosses a processor boundary until that button is pressed. Asking at
    # upload would ask about something that has not happened, and make the answer stale by the time
    # it matters (`ROADMAP.md` Phase 3 step 11).
    processing_consent_at: Mapped[timestamptz | None]
    processing_consent_version: Mapped[str | None] = mapped_column(String(16))

    # Set once every onboarding page is satisfied (`ONBOARDING_AND_PROCESSING.md` §2).
    #
    # Deliberately **not** the same thing as `active`. Finishing onboarding means the account is
    # configured; `active` means the scheduler may dispatch notifications, which only becomes true
    # once a model exists to rank with. Conflating them would start a 60-minute notify loop for an
    # account whose scorer has nothing to serve, and every cycle would fail identically.
    onboarding_completed_at: Mapped[timestamptz | None]

    # **Engagement, not login** — a deliberate departure from G11's "no login in N days".
    #
    # Delivery is Telegram, so a user who reads their feed every morning and applies to things may
    # never open the web app again after onboarding. Marking them dormant on login alone would pause
    # the feed of the most engaged users and tell nobody — precisely the silent failure class
    # OPERATIONS_PLAN.md §4 exists to prevent. Bumped by login *or* any Telegram action: applying,
    # skipping, dismissing, building a CV.
    last_active_at: Mapped[timestamptz | None]

    # The plan whose limits apply, and per-user grants layered over them. Both live here rather
    # than in code constants so adding a tier is an INSERT and a support grant is an UPDATE — never
    # a deploy (TENANT_SAFETY.md §3).
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), default="free",
                                         server_default="free")
    quota_overrides: Mapped[dict | None] = mapped_column(JSONB)

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
    # Optional per-category channels: `{"hu": ..., "linkedin": ..., "other": ...}`
    # (`services/notifier/routing.py`). A single feed of everything is a feed people stop reading,
    # and splitting it by the distinction they actually make when scanning is what keeps each
    # stream skimmable. **A missing route falls back to `telegram_chat_id`** — a routing table is a
    # convenience, and losing a job because a channel was not configured would be the feature
    # taking something away.
    telegram_routes: Mapped[dict | None] = mapped_column(JSONB)
    # The path Telegram posts this user's button taps to. **Per user, and random**, because the URL
    # is what identifies the account: every user runs their own bot, so a single shared webhook
    # path could not tell whose tap it was without trusting the request body. Paired with
    # Telegram's own `secret_token` header — the path can leak into a log, and the header cannot
    # say *which* user (`services/api/telegram_routes.py`).
    telegram_webhook_secret: Mapped[str | None] = mapped_column(String(64))

    # --- the three counters that keep scoring honest (see module docstring) ---
    model_version: Mapped[int] = mapped_column(Integer, default=0)
    profile_version: Mapped[str | None] = mapped_column(String(64))
    profile_generation: Mapped[int] = mapped_column(Integer, default=0)

    # The confirmed capture output (`libs.contracts.profile.ProfileSchema`), and the single source
    # of truth for every rendered artifact (PROFILE_CAPTURE_PLAN.md §5). JSONB rather than columns
    # for the same reason as `user_config`: read once per render, never in a hot predicate.
    profile: Mapped[dict | None] = mapped_column(JSONB)
    profile_schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)

    # The unconfirmed output of *process data*, waiting for the user to read it. Separate from
    # `profile` because applying it bumps the generation and invalidates every label the user has —
    # doing that on the strength of text nobody has reviewed is exactly what §5 forbids. Cleared on
    # confirm.
    profile_draft: Mapped[dict | None] = mapped_column(JSONB)
    # `capture` or `manual`. Recorded so the difference between a derived NOT-list and a
    # self-reported one is measurable rather than assumed.
    capture_source: Mapped[str | None] = mapped_column(String(16))

    # The two the dashboard compares to decide whether the account needs processing
    # (`ONBOARDING_AND_PROCESSING.md` §4). Timestamps rather than a flag: "your documents changed
    # after your last process" is derivable from them and a flag would need clearing by whoever
    # remembers.
    documents_changed_at: Mapped[timestamptz | None]
    processed_at: Mapped[timestamptz | None]

    # The rendered rubric. Stored rather than re-rendered on demand because `profile_version` is its
    # hash: keeping the exact bytes that produced the version is what makes a label's scope
    # auditable after a template change (PROFILE_CAPTURE_PLAN.md §6.1).
    rubric_md: Mapped[str | None] = mapped_column(Text)

    # The generation-grounding block (PROFILE_CAPTURE_PLAN.md §6.2). The single-tenant instance has
    # CLAUDE.md, auto-loaded by the CLI on every call; a hosted service has no CLI, so the
    # equivalent is assembled here and injected explicitly. Stored rather than re-rendered per call
    # so a generated document can name the exact bytes that grounded it.
    facts_md: Mapped[str | None] = mapped_column(Text)

    # Managed Agents agent objects are created with the *user's* API key, so they live in that
    # user's Anthropic workspace — one per user, not one per platform. Stored so re-capture reuses
    # it: "create the agent once" still holds, it is just once per user
    # (PROFILE_CAPTURE_PLAN.md §4.1). Anthropic ids are opaque; Text avoids bounding a string
    # whose length is not ours to bound.
    capture_agent_id: Mapped[str | None] = mapped_column(Text)

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
        # The webhook path *is* the identity of the account it belongs to, so two accounts sharing
        # one would attribute a button tap to whichever row the query returned first. Partial,
        # because most accounts have no webhook registered and NULLs are not the constraint's
        # business.
        Index("uq_users_webhook_secret", "telegram_webhook_secret", unique=True,
              postgresql_where=text("telegram_webhook_secret IS NOT NULL")),
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
