"""The account, the config form's contents, and the account's encrypted credentials."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import IndexModel

from libs.db import Document, now

USER_STATUSES = ("pending_verification", "active", "suspended", "deleted")
TELEGRAM_STATUSES = ("unconfigured", "connected", "broken")


@dataclass(kw_only=True)
class User(Document):
    COLLECTION = "users"
    OWNER = None
    CHOICES = {"status": USER_STATUSES, "telegram_status": TELEGRAM_STATUSES}  # noqa: RUF012
    INDEXES = (IndexModel("email", unique=True, name="email_unique"),
               IndexModel("linkedin_next_run_at", partialFilterExpression={"active": True},
                          name="linkedin_due"))

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
    profile_files_visible: bool = False
    # Per-account overrides of `libs.limits`; a key absent here means the catalogue's default.
    limits: dict = field(default_factory=dict)
    linkedin_interval_min: int = 240
    linkedin_next_run_at: datetime | None = None
    telegram_status: str = "unconfigured"
    telegram_chat_id: str | None = None
    # What the person's documents say about them, read by rules (`libs.profiles`).
    profile: dict | None = None
    documents_changed_at: datetime | None = None
    processed_at: datetime | None = None
    hard_data_changed_at: datetime | None = None
    # Green and red words suggested from their documents, with a reason each (`libs.suggestions`).
    suggestions: dict | None = None
    # This account's data key, wrapped by the platform key (`libs.secrets`).
    wrapped_dek: bytes | None = None
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


SECRET_KINDS = ("claude_api", "claude_oauth", "proxy", "gemini_api", "openai_api")


@dataclass(kw_only=True)
class UserSecret(Document):
    """One encrypted credential. Write-only from the site: it is never shown back."""

    COLLECTION = "user_secrets"
    CHOICES = {"kind": SECRET_KINDS}  # noqa: RUF012
    INDEXES = (IndexModel([("user_id", 1), ("kind", 1)], unique=True, name="one_per_kind"),)

    user_id: uuid.UUID
    kind: str
    ciphertext: bytes
    nonce: bytes
    last_4: str | None = None
    validated_at: datetime | None = None
    updated_at: datetime = field(default_factory=now)
