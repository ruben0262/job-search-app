"""Who pays for an AI request, and the client that makes the call on their account.

The owner's rules. A CV or cover letter is written only with the AI account the person has in use
(2026-11-09, `libs.llm.accounts`): a sign-in to their own Claude, ChatGPT or Gemini account, or an API
key; without one, none is made (`own`). The help chat runs on an API key in use, else on the
platform's key while their daily allowance lasts, else nobody, and the person is told so (`resolve`).

**These two are where it is decided.** The API asks when a document is requested and records the
answer on the queue row; the docgen worker then opens `paying_client` for exactly that answer rather
than deciding again. Whatever the platform's key pays for is counted in one place, `ai_requests`,
through `record_ai_request`.
"""
from __future__ import annotations

import os
import uuid as _uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database

from libs import limits
from libs.db import insert
from libs.db.models import AiRequest, User
from libs.db.models.user import KEY_KINDS
from libs.llm import accounts
from libs.llm.cli import CliClient
from libs.llm.client import AnthropicClient, LlmClient
from libs.llm.rest import GeminiClient, OpenAIClient
from libs.secrets import SecretStore, redacting

# What a request paid by the platform records as its payer, and how its key is configured.
PLATFORM = "platform"
PLATFORM_PROVIDER_ENV, PLATFORM_KEY_ENV = "PLATFORM_AI_PROVIDER", "PLATFORM_AI_KEY"
_PLATFORM_KIND = {"anthropic": "claude_api", "gemini": "gemini_api", "openai": "openai_api"}


@dataclass(frozen=True)
class Payer:
    # The id of the AI account in use, or `PLATFORM`.
    kind: str
    provider: str


def own(db: Database, user: User) -> Payer | None:
    """The account a CV or cover letter is written with: the one they have in use, or None."""
    account = accounts.in_use(db, user.id)
    return None if account is None else Payer(str(account.id), accounts.PROVIDER[account.kind])


def resolve(db: Database, user: User) -> Payer | None:
    """Who pays for one help-chat answer, or `None` when nobody can.

    Their API key in use first: the chat calls tools, which a signed-in account's command-line tool does
    not offer (`libs.llm.cli`). Else the platform, only when it has a key configured, and only while
    today's `ai_requests_today` is under `ai_platform_requests_per_day`.
    """
    account = accounts.in_use(db, user.id)
    if account is not None and account.kind in KEY_KINDS:
        return Payer(str(account.id), accounts.PROVIDER[account.kind])
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
    """The client for one API key of one kind: Claude, Gemini or OpenAI."""
    if kind == "gemini_api":
        return GeminiClient(value)
    if kind == "openai_api":
        return OpenAIClient(value)
    return AnthropicClient(value)


@contextmanager
def paying_client(store: SecretStore, user_id: _uuid.UUID,
                  payer: str) -> Iterator[tuple[LlmClient, str] | None]:
    """The client on the account `own` or `resolve` chose and its provider, or `None` if that account
    has gone. Its secret is scrubbed from every log line for as long as the client is held, and a
    sign-in its tool renewed during the call is sealed again for the next one."""
    if payer == PLATFORM:
        provider = os.environ.get(PLATFORM_PROVIDER_ENV, "")
        kind, value = _PLATFORM_KIND.get(provider), os.environ.get(PLATFORM_KEY_ENV, "")
    else:
        try:
            account = ObjectId(payer)
        except (InvalidId, TypeError):
            yield None
            return
        secret = store.reveal_account(user_id, account)
        kind, value = (secret.kind, secret.reveal()) if secret else (None, "")
        provider = accounts.PROVIDER.get(kind or "", "")
    if not (kind and value):
        yield None
        return
    with redacting(value):
        if kind in KEY_KINDS:
            yield client_for(value, kind), provider
        else:
            yield CliClient(kind, value, lambda renewed: store.reseal(user_id, account, renewed)), provider
