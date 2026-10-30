"""Per-user repository, and the isolation guarantee it exists to enforce."""
from __future__ import annotations

import uuid

import pytest

from libs.db import get, insert
from libs.db.models import User, UserConfig, UserSecret
from libs.tenant.repository import TenantRepository


@pytest.fixture()
def t(db):
    return TenantRepository(db)


def _user(db, **fields) -> User:
    return insert(db, User(email=f"{uuid.uuid4()}@example.test", password_hash="x",
                           timezone="Europe/Budapest", **fields))


# ── config, secrets ───────────────────────────────────────────────────────────────────────────

def test_config_round_trips_and_records_its_schema_version(db, t):
    """`schema_version` is what makes a future shape change migratable rather than guesswork."""
    from libs.contracts.config import CONFIG_SCHEMA_VERSION

    u = _user(db)
    t.save_config(u.id, {"keywords": ["python"], "locations": ["london"]})

    assert t.get_config(u.id) == {"keywords": ["python"], "locations": ["london"]}
    stored = db[UserConfig.COLLECTION].find_one({"user_id": u.id})
    assert stored["schema_version"] == CONFIG_SCHEMA_VERSION

    t.save_config(u.id, {"keywords": ["golang"]})
    assert t.get_config(u.id) == {"keywords": ["golang"]}, "save must replace, not accumulate"
    assert db[UserConfig.COLLECTION].count_documents({"user_id": u.id}) == 1


def test_a_user_without_a_config_reads_as_none_not_as_empty(db, t):
    """`None` and `{}` mean different things: not onboarded yet, versus onboarded with nothing."""
    assert t.get_config(_user(db).id) is None


def test_only_active_users_configs_reach_the_harvest_fold(db, t):
    """A suspended or unverified account must not consume harvest budget."""
    active = _user(db, active=True, status="active")
    inactive = _user(db, active=False, status="pending_verification")
    suspended = _user(db, active=True, status="suspended")

    for u in (active, inactive, suspended):
        t.save_config(u.id, {"keywords": ["python"]})

    configs = t.active_configs()
    assert configs[active.id] == {"keywords": ["python"]}
    assert inactive.id not in configs
    assert suspended.id not in configs


def test_secret_metadata_never_exposes_the_value(db, t):
    """Secrets are write-only through the API (`TENANT_SAFETY.md` §2)."""
    u = _user(db)
    insert(db, UserSecret(user_id=u.id, kind="claude_api", ciphertext=b"SECRET-CIPHERTEXT",
                          nonce=b"nonce", last_4="ab12"))

    (meta,) = t.secret_metadata(u.id)
    assert meta.kind == "claude_api"
    assert meta.last_4 == "ab12"

    rendered = repr(meta) + str(meta)
    assert "SECRET-CIPHERTEXT" not in rendered
    assert not hasattr(meta, "ciphertext")
    assert not hasattr(meta, "nonce")


# ── T1: tenant isolation. Release blocker. ────────────────────────────────────────────────────

def test_one_users_config_is_never_readable_by_another(db, t):
    """T1: config read path."""
    alice, bob = _user(db), _user(db)
    t.save_config(alice.id, {"keywords": ["alice-secret-term"]})
    t.save_config(bob.id, {"keywords": ["bob-term"]})

    assert t.get_config(alice.id) == {"keywords": ["alice-secret-term"]}
    assert t.get_config(bob.id) == {"keywords": ["bob-term"]}


def test_one_users_secrets_are_never_listed_for_another(db, t):
    """T1: secrets read path. These are strangers' live credentials."""
    alice, bob = _user(db), _user(db)
    for owner, kind, last_4 in ((alice, "claude_api", "aaaa"), (bob, "claude_api", "bbbb"),
                                (bob, "proxy", "cccc")):
        insert(db, UserSecret(user_id=owner.id, kind=kind, ciphertext=last_4.encode(), nonce=b"n",
                              last_4=last_4))

    assert [m.last_4 for m in t.secret_metadata(alice.id)] == ["aaaa"]
    assert sorted(m.last_4 for m in t.secret_metadata(bob.id)) == ["bbbb", "cccc"]


# ── account lifecycle: reported, never automatic (ROADMAP 6c) ─────────────────────────────────

def test_no_mechanism_pauses_an_account_for_being_quiet():
    """Removed 2026-08-11, and this fails if it comes back by another name.

    `mark_dormant` paused an account after 30 quiet days and the login path restored `status` while
    leaving `active` False — an account that reads as live and puts no keywords into the harvest
    fold. The replacement is a number in the accounts list and an operator's judgement.
    """
    assert not hasattr(TenantRepository, "mark_dormant")
    assert not hasattr(TenantRepository, "reactivate")
    assert not hasattr(TenantRepository, "touch_activity")

    from libs.db.models.user import USER_STATUSES
    assert "dormant" not in USER_STATUSES


def test_a_quiet_account_keeps_running_and_keeps_its_keywords(db, t):
    """The behaviour that changed: quiet is no longer a reason to stop harvesting for somebody."""
    from datetime import timedelta

    from libs.db import now

    quiet = _user(db, status="active", active=True, last_active_at=now() - timedelta(days=45))
    t.save_config(quiet.id, {"keywords": ["python"]})

    assert get(db, User, quiet.id).last_active_at < now() - timedelta(days=44)
    assert quiet.id in t.active_configs(), (
        "45 days of silence must not remove an account from the harvest fold — an operator deletes "
        "it deliberately or it keeps working")
