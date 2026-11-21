"""A plan's limits are read from its provider once in a while, not on every page (`libs.llm.limits.held`)."""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest

from tests.conftest import needs_db

CLAUDE_LOGIN = '{".credentials.json": "{}"}'


@pytest.fixture()
def api(db, monkeypatch):
    """The real app, somebody signed in with a Claude sign-in linked, and a provider that counts its calls."""
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    from fastapi.testclient import TestClient

    from libs.db import insert, now
    from libs.db.models import User
    from libs.llm import limits
    from libs.secrets import KeyRing, SecretStore, generate_key
    from services.api import deps
    from services.api.app import app
    from tests.conftest import access_cookie

    asked: list[str] = []

    def provider(kind, blob, **kw):
        asked.append(kind)
        return [limits.Window("five_hour", "5-hour limit", 30.0 + len(asked), now())]

    monkeypatch.setattr(limits, "read", provider)
    people = [insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                              subscription_status="active", status="active", onboarding_completed_at=now()))
              for _ in range(2)]
    store = SecretStore(db, KeyRing.from_env({"SECRETS_KEK": f"k1:{generate_key()}"}))
    account = store.add(people[0].id, "claude_login", CLAUDE_LOGIN, label="Claude Max · sam@example.com")
    app.dependency_overrides[deps.get_store] = lambda: store
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", access_cookie(db, people[0].id), path="/")
            yield http, db, people, account, asked
    finally:
        app.dependency_overrides.clear()


def _used(http, account) -> float:
    return http.get(f"/ai-accounts/{account}/limits").json()["windows"][0]["percent"]


@needs_db
def test_a_plans_limits_are_asked_of_its_provider_once_in_a_while_and_again_after_a_document(api):
    """**A relationship, not a stopwatch.** The site reads every plan's limits ahead on each page it loads,
    so that Settings shows them at once, and each read was a call to Anthropic or ChatGPT with the person's
    own token, holding a worker for up to eight seconds: a call a page, for windows five hours and a week
    long. The provider is asked once and its answer kept (`LIMITS_KEEP`); asked again when that has passed,
    and at once when a document has been made here since, which is what moves them from here and when the
    site shows them (owner, 2026-11-19: "read then"). Time is moved by ageing the row, not by waiting."""
    from libs.db import insert, now
    from libs.db.models import QueueJob, UserSecret
    from libs.llm.limits import LIMITS_KEEP

    http, db, people, account, asked = api

    assert [_used(http, account) for _ in range(4)] == [31.0] * 4
    assert len(asked) == 1, "four pages, one question"

    db[UserSecret.COLLECTION].update_one({"_id": account}, {"$set": {"limits_at": now() - LIMITS_KEEP * 1.1}})
    assert (_used(http, account), _used(http, account)) == (32.0, 32.0)
    assert len(asked) == 2, "asked again once the answer is older than it is kept for"

    insert(db, QueueJob(lane="docgen", user_id=people[0].id, state="done", finished_at=now()))
    assert _used(http, account) == 33.0, "a document made since: read then"
    db[QueueJob.COLLECTION].update_many({}, {"$set": {"finished_at": now() - timedelta(minutes=1)}})
    assert _used(http, account) == 33.0 and len(asked) == 3, "and kept from then on"


@needs_db
def test_what_is_kept_of_one_persons_limits_is_never_told_to_another(api):
    from tests.conftest import access_cookie

    http, db, people, account, asked = api
    assert _used(http, account) == 31.0

    http.cookies.set("access_token", access_cookie(db, people[1].id), path="/")

    assert http.get(f"/ai-accounts/{account}/limits").status_code == 404
    assert len(asked) == 1, "and nothing was asked on their behalf"
