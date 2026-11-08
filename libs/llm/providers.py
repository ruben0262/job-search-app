"""Who pays for an AI request, and the client that makes the call on their key.

The owner's rule (ROADMAP Phase 8): the person's own key first — Claude, Gemini or OpenAI — else the
platform's key while their daily allowance lasts, else nobody, and the person is told so.
No free tier, and nothing that multiplies a provider's quota.

**`resolve` is the one place that is decided.** The API asks it when a build is requested and
records the answer on the queue row; the docgen worker then opens `paying_client` for exactly that
answer rather than deciding again. Whatever the platform's key pays for — a document now, a chat
reply next — is counted in one place, `ai_requests`, through `record_ai_request`.
"""
from __future__ import annotations

import os
import uuid as _uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pymongo.database import Database

from libs import limits
from libs.db import insert
from libs.db.models import AiRequest, User, UserSecret
from libs.llm.client import AnthropicClient, LlmClient
from libs.llm.rest import GeminiClient, OpenAIClient
from libs.secrets import SecretStore, redacting

# The person's own keys, in the order they are tried, and whose models each one runs.
PROVIDERS = {"claude_api": "anthropic", "claude_oauth": "anthropic",
             "gemini_api": "gemini", "openai_api": "openai"}
OWN_KEYS = tuple(PROVIDERS)

# What a build paid by the platform records as its payer, and how its key is configured.
PLATFORM = "platform"
PLATFORM_PROVIDER_ENV, PLATFORM_KEY_ENV = "PLATFORM_AI_PROVIDER", "PLATFORM_AI_KEY"
_PLATFORM_KIND = {"anthropic": "claude_api", "gemini": "gemini_api", "openai": "openai_api"}


@dataclass(frozen=True)
class Payer:
    # One of `OWN_KEYS`, or `PLATFORM`.
    kind: str
    provider: str


def resolve(db: Database, user: User) -> Payer | None:
    """Who pays for one AI request from this person, or `None` when nobody can.

    The platform pays only for an account with no key of its own, only when it has a key configured,
    and only while today's `ai_requests_today` is under `ai_platform_requests_per_day`.
    """
    held = set(db[UserSecret.COLLECTION].distinct(
        "kind", {"user_id": user.id, "kind": {"$in": list(OWN_KEYS)}}))
    for kind in OWN_KEYS:
        if kind in held:
            return Payer(kind, PROVIDERS[kind])

    if platform_left(db, user) > 0:
        return Payer(PLATFORM, os.environ[PLATFORM_PROVIDER_ENV])
    return None


def platform_left(db: Database, user: User) -> int:
    """How many more requests the platform's key will pay for, for this person, today: 0 when the
    platform has no key configured. What `resolve` decides on, and what a page shows as left."""
    allowance = limits.resolve(user, "ai_platform_requests_per_day")
    configured = os.environ.get(PLATFORM_PROVIDER_ENV) in _PLATFORM_KIND and os.environ.get(PLATFORM_KEY_ENV)
    if not configured or allowance is None:
        return 0
    return max(0, allowance - ai_requests_today(db, user.id))


def ai_requests_today(db: Database, user_id: _uuid.UUID) -> int:
    """How many AI requests the platform's key has paid for, for this person, since their midnight."""
    return db[AiRequest.COLLECTION].count_documents(
        {"user_id": user_id, "created_at": {"$gte": limits.their_midnight(db, user_id)}})


def record_ai_request(db: Database, user_id: _uuid.UUID) -> None:
    """Count one request against this person's allowance. Call it once `resolve` has said
    `PLATFORM` and the request is going ahead."""
    insert(db, AiRequest(user_id=user_id))


def client_for(value: str, kind: str) -> LlmClient:
    """The client for one key of one kind: a Claude key or subscription, or a Gemini or OpenAI key."""
    if kind == "gemini_api":
        return GeminiClient(value)
    if kind == "openai_api":
        return OpenAIClient(value)
    return AnthropicClient(value, kind)


@contextmanager
def paying_client(store: SecretStore, user_id: _uuid.UUID,
                  payer: str) -> Iterator[tuple[LlmClient, str] | None]:
    """The client on the key `resolve` chose and its provider, or `None` if that key has gone.

    The key is scrubbed from every log line for as long as the client is held.
    """
    if payer == PLATFORM:
        provider = os.environ.get(PLATFORM_PROVIDER_ENV, "")
        kind, value = _PLATFORM_KIND.get(provider), os.environ.get(PLATFORM_KEY_ENV, "")
    else:
        kind, provider = payer, PROVIDERS.get(payer, "")
        secret = store.reveal(user_id, payer) if provider else None
        value = secret.reveal() if secret else ""
    if not (kind and value):
        yield None
        return
    with redacting(value):
        yield client_for(value, kind), provider
