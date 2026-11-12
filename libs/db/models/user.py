"""The account, the config form's contents, and the account's encrypted credentials."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from bson import ObjectId
from pymongo import IndexModel

from libs.db import Document, now

USER_STATUSES = ("pending_verification", "active", "suspended", "deleted")
# "waiting": their bot's token is saved and nobody has pressed Start in it yet.
TELEGRAM_STATUSES = ("unconfigured", "waiting", "connected", "broken")


@dataclass(kw_only=True)
class User(Document):
    COLLECTION = "users"
    OWNER = None
    CHOICES = {"status": USER_STATUSES, "telegram_status": TELEGRAM_STATUSES}  # noqa: RUF012
    INDEXES = (
        IndexModel("email", unique=True, name="email_unique"),
        # How a payment Stripe reports finds its account, and one account to a Stripe customer.
        IndexModel("stripe_customer_id", unique=True, name="one_customer",
                   partialFilterExpression={"stripe_customer_id": {"$type": "string"}}),
    )
    # Whether the person could open the profile files the setup agents wrote: nothing read it once the
    # file tiles left the site, and the admin's switch for it went on 2026-11-04. And when the account's
    # own LinkedIn search was next due: LinkedIn is read for everybody at once since 2026-11-04.
    RETIRED = ("profile_files_visible", "linkedin_next_run_at")

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str
    password_hash: str
    status: str = "pending_verification"
    email_verified_at: datetime | None = None
    full_name: str | None = None
    links: list | None = None
    processing_consent_at: datetime | None = None
    processing_consent_version: str | None = None
    onboarding_completed_at: datetime | None = None
    last_active_at: datetime | None = None
    timezone: str = "UTC"
    active: bool = False
    is_admin: bool = False
    # Per-account overrides of `libs.limits`; a key absent here means the catalogue's default.
    limits: dict = field(default_factory=dict)
    telegram_status: str = "unconfigured"
    telegram_chat_id: str | None = None
    # Their own bot's @name, from Telegram when its token was saved: where the page sends them to press Start.
    telegram_bot_name: str | None = None
    # When their chat was connected: alerts start from then, not with every match already waiting.
    telegram_connected_at: datetime | None = None
    # A daily email of their new jobs (owner's decision, 2026-11-01: one summary a day, since every
    # email shares the mailbox's daily cap with sign-up mail), and when the last one was sent.
    email_alerts: bool = False
    email_digest_at: datetime | None = None
    # The old profile schema: nothing reads it (tests/test_columns_have_producers.py, KNOWN_UNWRITTEN).
    profile: dict | None = None
    documents_changed_at: datetime | None = None
    processed_at: datetime | None = None
    hard_data_changed_at: datetime | None = None
    # Green and red words suggested from their documents, with a reason each (`libs.suggestions`).
    suggestions: dict | None = None
    # The Jobs page's filters as they left them, so it opens the same (owner, 2026-11-07); `JobFilters` in
    # the API. None until they change one: the defaults.
    job_filters: dict | None = None
    # The look of every CV and of every cover letter made for them: an id in `libs.templates.CATALOGUE`,
    # picked in My documents (owner, 2026-11-07). None until they pick one: `libs.templates.DEFAULT`.
    cv_template: str | None = None
    cover_template: str | None = None
    # This account's data key, wrapped by the platform key (`libs.secrets`).
    wrapped_dek: bytes | None = None
    # Their membership: a Stripe subscription, seven days free and then billed (owner, 2026-11-11). Stripe is
    # the record; these are its answer when last asked (`billing_synced_at`), written only by
    # `services.api.billing_routes.sync` and read by `libs.membership`. Their customer at Stripe, made the
    # first time the payment form opens for them.
    stripe_customer_id: str | None = None
    # Their newest subscription and Stripe's word for it: trialing, active, past_due, unpaid, paused,
    # canceled, incomplete or incomplete_expired. None: they never had one. Not in CHOICES: the word is
    # Stripe's, and one it adds must still be copied, where it reads as not paying.
    subscription_id: str | None = None
    subscription_status: str | None = None
    trial_ends_at: datetime | None = None
    # The end of the period paid for, or of the trial: when the next payment is taken, unless it ends.
    period_ends_at: datetime | None = None
    # When it stops, because they cancelled it: it runs until then and nothing more is charged.
    cancels_at: datetime | None = None
    # Their last payment that took money (a trial's 0 is not one), in the smallest unit of `currency`.
    last_paid_at: datetime | None = None
    last_paid_amount: int | None = None
    currency: str | None = None
    billing_synced_at: datetime | None = None
    # When an admin let them use the whole site without a membership, whatever Stripe says (owner,
    # 2026-11-11). None: not let in.
    free_access_at: datetime | None = None
    # When they picked the free plan on the page that comes after the email link and before setting up
    # (owner, 2026-11-12; `POST /billing/free`). None: not picked, or picked the membership, whose
    # subscription says so (`libs.membership.choosing`).
    plan_chosen_at: datetime | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass(kw_only=True)
class UserConfig(Document):
    """The config form's contents: sections of green and red words, places, languages, remote policy."""

    COLLECTION = "user_config"
    INDEXES = (IndexModel("user_id", unique=True, name="one_per_user"),)

    user_id: uuid.UUID
    config: dict
    schema_version: int = 1
    updated_at: datetime = field(default_factory=now)


# The AI accounts a person links in Settings (owner, 2026-11-09): a sign-in to their own Claude,
# ChatGPT (Codex) or Google (Gemini) account, kept as the files that account's own command-line tool
# writes, or an API key. Several of each, one in use (`libs.llm.accounts`).
LOGIN_KINDS = ("claude_login", "codex_login", "gemini_login")
KEY_KINDS = ("claude_api", "openai_api", "gemini_api")
AI_KINDS = (*LOGIN_KINDS, *KEY_KINDS)
# Everything encrypted here: those, and the token of the person's own Telegram bot, which only the
# Telegram setup (`services/api/telegram_routes.py`) stores, one per person.
SECRET_KINDS = (*AI_KINDS, "telegram_bot")


@dataclass(kw_only=True)
class UserSecret(Document):
    """One encrypted credential. Write-only from the site: it is never shown back, not even in part."""

    COLLECTION = "user_secrets"
    CHOICES = {"kind": SECRET_KINDS}  # noqa: RUF012
    # The last four characters were shown back until 2026-11-09 (owner: nothing of a secret is shown).
    RETIRED = ("last_4",)
    INDEXES = (
        IndexModel([("user_id", 1), ("kind", 1)], unique=True, name="one_bot",
                   partialFilterExpression={"kind": "telegram_bot"}),
        # The one AI account their documents are written with.
        IndexModel([("user_id", 1)], unique=True, name="one_in_use",
                   partialFilterExpression={"active": True}),
    )

    id: ObjectId = field(default_factory=ObjectId)
    user_id: uuid.UUID
    kind: str
    ciphertext: bytes
    nonce: bytes
    # What Settings calls an AI account: "Claude Max · name@example.com", "OpenAI key".
    label: str | None = None
    active: bool = False
    validated_at: datetime | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)
