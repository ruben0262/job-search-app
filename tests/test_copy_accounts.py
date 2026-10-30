"""The one-time copy of accounts from the old Postgres into MongoDB (`deploy/scripts/copy_accounts.py`).

The conversion rules need no database and always run. The full copy needs a Postgres at the final
schema, which nothing in this suite provides: set `POSTGRES_COPY_SOURCE` to an empty database you
upgraded from the tag (`git worktree add /tmp/tag model-on-postgres`, then `alembic upgrade head` in
it), and the test seeds it from `tests/fixtures/old_accounts.sql`.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import uuid
from decimal import Decimal

import pytest

from tests.conftest import needs_db

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _script():
    spec = importlib.util.spec_from_file_location(
        "copy_accounts", ROOT / "deploy" / "scripts" / "copy_accounts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_what_is_not_carried_over_is_dropped_and_the_rest_keeps_its_values():
    from libs.db.models import PlatformSetting, ProxyCredential, SetupMessage, User, UserSecret

    copy = _script()
    user = copy.converted(User, {
        "id": ALICE, "email": "a@example.com", "password_hash": "$argon2id$x", "status": "active",
        "email_verified_at": None, "full_name": None, "links": None, "processing_consent_at": None,
        "processing_consent_version": None, "onboarding_completed_at": None, "last_active_at": None,
        "timezone": "UTC", "active": True, "is_admin": False, "profile_files_visible": False,
        "limits": {"keywords": 40, "label_pool": 300}, "linkedin_interval_min": 240,
        "linkedin_next_run_at": None, "telegram_status": "unconfigured", "telegram_chat_id": None,
        "profile": None, "documents_changed_at": None, "processed_at": None, "hard_data_changed_at": None,
        "suggestions": None, "wrapped_dek": memoryview(b"\xde\xad"), "created_at": None, "updated_at": None})
    assert user["_id"] == ALICE and user["password_hash"] == "$argon2id$x"
    assert user["limits"] == {"keywords": 40}, "a limit the catalogue no longer has governs nothing"
    assert user["wrapped_dek"] == b"\xde\xad"

    assert copy.converted(UserSecret, {"user_id": ALICE, "kind": "telegram_bot", "ciphertext": b"",
                                       "nonce": b"", "last_4": None, "validated_at": None,
                                       "updated_at": None}) is None
    assert copy.converted(PlatformSetting, {"id": "embed_batch_size", "value": 64, "updated_at": None,
                                            "updated_by": None}) is None
    proxy = copy.converted(ProxyCredential, {
        "user_id": ALICE, "secret_ref": "r", "daily_cap_mb": 200, "mb_used_today": Decimal("12.5"),
        "usage_reset_at": None, "status": "healthy", "fail_streak": 0, "last_error": None,
        "last_used_at": None, "created_at": None, "updated_at": None})
    assert proxy["mb_used_today"] == 12.5 and isinstance(proxy["mb_used_today"], float)
    reply = copy.converted(SetupMessage, {
        "id": 21, "user_id": ALICE, "conversation_id": ALICE, "role": "assistant", "content": "Gre",
        "status": "streaming", "error": None, "agent": "support", "tool_calls": None, "created_at": None})
    assert reply["status"] == "failed", "nothing will finish a reply whose writer was the old process"


@needs_db
@pytest.mark.skipif(not os.environ.get("POSTGRES_COPY_SOURCE"),
                    reason="POSTGRES_COPY_SOURCE not set (an empty Postgres at the tag's final schema)")
def test_a_copied_account_signs_in_with_its_own_password_and_keeps_its_words(db, monkeypatch):
    import psycopg
    from psycopg.rows import dict_row

    from libs.db import find, get, insert
    from libs.db.models import MODELS, User, UserDocument, UserSecret
    from libs.tenant.repository import TenantRepository

    url = os.environ["POSTGRES_COPY_SOURCE"]
    with psycopg.connect(url, autocommit=True) as source:
        source.execute((ROOT / "tests" / "fixtures" / "old_accounts.sql").read_text())
    try:
        monkeypatch.setenv("POSTGRES_URL", url)
        assert _script().main([]) == 0

        for model in MODELS:  # every document loads through its own class
            find(db, model, {})
        alice = get(db, User, ALICE)
        assert alice.password_hash.startswith("$argon2id") and alice.telegram_status == "connected"
        assert TenantRepository(db).get_config(ALICE)["sections"][0]["keywords"] == ["backend engineer"]
        assert [secret.kind for secret in find(db, UserSecret, {"user_id": ALICE})] == ["claude_api"]
        assert db.corpus.count_documents({}) == 0, "postings are collected again, never copied"
        assert insert(db, UserDocument(user_id=ALICE, kind="other", filename="n", text="n",
                                       text_sha256="n", chars=1)).id > 5, "ids continue past the copied ones"
        assert _script().main([]) == 2, "a second copy is refused"
    finally:
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as source:
            for table in ("lane_pauses", "corpus", "setup_messages", "setup_conversations",
                          "user_notifications",
                          "proxy_credentials", "push_subscriptions", "user_profile_files", "user_documents",
                          "user_secrets", "user_config", "users", "operator_keywords", "ats_tokens",
                          "platform_settings"):
                source.execute(f"DELETE FROM {table}")  # noqa: S608 - a fixed list of table names
