"""T4 — a user's credentials never leave the system in readable form."""
from __future__ import annotations

import base64
import dataclasses
import io
import logging
import os
import uuid

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("sqlalchemy")
from libs.db.models import User, UserSecret
from libs.secrets import (
    MASK,
    DecryptionFailed,
    KeyRing,
    KeyUnavailable,
    Secret,
    SecretsError,
    SecretStore,
    UnknownSecretKind,
    generate_key,
    install_on_handler,
    redact,
    redacting,
)
from libs.secrets.keyring import open_sealed, seal
from libs.tenant.repository import TenantRepository

# Shaped like the real thing, because the pattern-based half of the redaction filter recognises credential.
CLAUDE_KEY = "sk-ant-api03-" + "Zx9" * 20
TELEGRAM_TOKEN = "7123456789:AAF" + "q7Kd2mNp" * 4
PROXY_URL = "http://tenant-alice:s3cr3t-p4ssw0rd@gate.smartproxy.test:7000"


def _ring(*ids: str) -> KeyRing:
    """A ring with real random keys. Never a fixed test key — see `test_no_hardcoded_fallback`."""
    entries = ",".join(f"{key_id}:{generate_key()}" for key_id in (ids or ("k1",)))
    return KeyRing.from_env({"SECRETS_KEK": entries})


# ══ 1. the cryptography: a dump without the KEK is unreadable ═══════════════════════════════════

def test_a_sealed_secret_round_trips():
    ring, user = _ring(), uuid.uuid4()
    dek = ring.new_dek()
    ciphertext, nonce = seal(dek, CLAUDE_KEY, user_id=user, kind="claude_api")
    assert open_sealed(dek, ciphertext, nonce, user_id=user, kind="claude_api") == CLAUDE_KEY


def test_the_plaintext_does_not_survive_anywhere_in_the_ciphertext():
    """The most basic dump property, and the one a broken encryption path passes every other test while."""
    ring, user = _ring(), uuid.uuid4()
    ciphertext, nonce = seal(ring.new_dek(), CLAUDE_KEY, user_id=user, kind="claude_api")
    blob = ciphertext + nonce
    assert CLAUDE_KEY.encode() not in blob
    assert base64.b64encode(CLAUDE_KEY.encode()) not in blob


def test_sealing_the_same_value_twice_produces_different_ciphertext():
    """A fresh nonce per call."""
    ring, user = _ring(), uuid.uuid4()
    dek = ring.new_dek()
    first, _ = seal(dek, CLAUDE_KEY, user_id=user, kind="claude_api")
    second, _ = seal(dek, CLAUDE_KEY, user_id=user, kind="claude_api")
    assert first != second


def test_a_ciphertext_cannot_be_moved_to_another_user():
    """An attacker with database *write* access, not key access."""
    ring = _ring()
    dek = ring.new_dek()
    alice, mallory = uuid.uuid4(), uuid.uuid4()
    ciphertext, nonce = seal(dek, CLAUDE_KEY, user_id=alice, kind="claude_api")

    with pytest.raises(DecryptionFailed):
        open_sealed(dek, ciphertext, nonce, user_id=mallory, kind="claude_api")


def test_a_ciphertext_cannot_be_relabelled_as_another_kind():
    """Relabelling a proxy password as a Claude key would hand a worker a credential it will send to the."""
    ring, user = _ring(), uuid.uuid4()
    dek = ring.new_dek()
    ciphertext, nonce = seal(dek, PROXY_URL, user_id=user, kind="proxy")

    with pytest.raises(DecryptionFailed):
        open_sealed(dek, ciphertext, nonce, user_id=user, kind="claude_api")


def test_tampering_with_the_ciphertext_is_detected():
    """GCM authenticates. A flipped bit must fail, not decrypt to garbage a caller then uses."""
    ring, user = _ring(), uuid.uuid4()
    dek = ring.new_dek()
    ciphertext, nonce = seal(dek, CLAUDE_KEY, user_id=user, kind="claude_api")
    corrupted = bytes([ciphertext[0] ^ 0x01]) + ciphertext[1:]

    with pytest.raises(DecryptionFailed):
        open_sealed(dek, corrupted, nonce, user_id=user, kind="claude_api")


def test_a_wrapped_dek_is_useless_to_a_different_kek():
    """The whole claim."""
    user = uuid.uuid4()
    theirs, ours = _ring(), _ring()
    wrapped = ours.wrap_dek(ours.new_dek(), user_id=user)

    with pytest.raises((DecryptionFailed, KeyUnavailable)):
        theirs.unwrap_dek(wrapped, user_id=user)


def test_no_hardcoded_fallback_when_the_kek_is_missing():
    """A default key would make every test above pass and the design worthless."""
    with pytest.raises(KeyUnavailable):
        KeyRing.from_env({})
    with pytest.raises(KeyUnavailable):
        KeyRing.from_env({"SECRETS_KEK": "   "})


def test_a_malformed_kek_is_rejected_rather_than_padded_to_length():
    """Silently accepting a short key would produce a working system with a weak one."""
    short = base64.urlsafe_b64encode(b"too short").decode().rstrip("=")
    with pytest.raises(KeyUnavailable):
        KeyRing.from_env({"SECRETS_KEK": f"k1:{short}"})
    with pytest.raises(KeyUnavailable):
        KeyRing.from_env({"SECRETS_KEK": "k1:not-base64!!"})


# ══ 2. KEK rotation: possible without re-encrypting anything ═══════════════════════════════════

def test_a_retired_kek_still_reads_rows_it_wrapped():
    """Rotation is not an outage."""
    user = uuid.uuid4()
    old = generate_key()
    ring_before = KeyRing.from_env({"SECRETS_KEK": f"k1:{old}"})
    wrapped = ring_before.wrap_dek(dek := ring_before.new_dek(), user_id=user)

    after = KeyRing.from_env({"SECRETS_KEK": f"k2:{generate_key()},k1:{old}"})
    assert after.unwrap_dek(wrapped, user_id=user) == dek
    assert after.needs_rewrap(wrapped) is True

    rewrapped = after.wrap_dek(dek, user_id=user)
    assert after.needs_rewrap(rewrapped) is False


def test_dropping_a_key_that_is_still_in_use_says_so():
    """The recoverable failure."""
    user = uuid.uuid4()
    old_ring = _ring()
    wrapped = old_ring.wrap_dek(old_ring.new_dek(), user_id=user)

    with pytest.raises(KeyUnavailable) as exc:
        _ring("k9").unwrap_dek(wrapped, user_id=user)
    assert "SECRETS_KEK" in str(exc.value)


# ══ 3. a Secret does not stringify ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "render",
    [str, repr, lambda s: f"{s}", lambda s: f"{s!r}", lambda s: format(s, ""),
     # Percent-formatting on purpose, not an oversight: it is what `logging` itself does to a `%s` argument.
     lambda s: "%s" % (s,)])  # noqa: UP031
def test_no_way_of_rendering_a_secret_shows_the_value(render):
    """The type-level half of "never logged"."""
    secret = Secret(CLAUDE_KEY, kind="claude_api")
    rendered = render(secret)
    assert CLAUDE_KEY not in rendered
    assert secret.last_4 in rendered, "unrecognisable is not the goal — last_4 must survive"


def test_a_secret_inside_a_container_still_does_not_leak():
    """Credentials travel inside dicts and dataclasses; the container's repr is what reaches a log."""
    payload = {"claude": Secret(CLAUDE_KEY, kind="claude_api"), "user": "alice"}
    assert CLAUDE_KEY not in repr(payload)


def test_reveal_is_the_only_way_out():
    assert Secret(CLAUDE_KEY, kind="claude_api").reveal() == CLAUDE_KEY


# ══ 4. a known secret cannot appear in captured logs ═══════════════════════════════════════════

@pytest.fixture()
def captured():
    """A logger with a real handler, filtered the way `install()` does it."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    install_on_handler(handler)

    root = logging.getLogger("t4")
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield root, stream
    finally:
        root.removeHandler(handler)


@pytest.mark.parametrize("value", [CLAUDE_KEY, TELEGRAM_TOKEN])
def test_a_live_secret_is_scrubbed_from_a_log_message(captured, value):
    logger, stream = captured
    with redacting(value):
        logger.info("calling upstream with %s", value)
    output = stream.getvalue()
    assert value not in output
    assert MASK in output


def test_a_secret_is_scrubbed_from_a_child_loggers_record(captured):
    """The propagation case."""
    _, stream = captured
    child = logging.getLogger("t4.services.notifier")
    with redacting(TELEGRAM_TOKEN):
        child.warning("telegram rejected token %s", TELEGRAM_TOKEN)
    assert TELEGRAM_TOKEN not in stream.getvalue()


def test_a_secret_is_scrubbed_from_a_traceback(captured):
    """Where credentials actually leak."""
    logger, stream = captured
    with redacting(PROXY_URL):
        try:
            raise ConnectionError(f"failed to connect to {PROXY_URL}")
        except ConnectionError:
            logger.exception("linkedin fetch failed")
    output = stream.getvalue()
    assert "s3cr3t-p4ssw0rd" not in output
    assert "linkedin fetch failed" in output, "redaction must not swallow the diagnostic"


def test_a_secret_carried_by_an_object_argument_is_scrubbed(captured):
    """`logger.info("config: %s", cfg)` where cfg is a dict holding a proxy URL."""
    logger, stream = captured
    with redacting(PROXY_URL):
        logger.info("proxy config: %s", {"url": PROXY_URL, "user_id": "alice"})
    assert "s3cr3t-p4ssw0rd" not in stream.getvalue()


def test_credential_shapes_are_masked_even_when_never_registered(captured):
    """The registry cannot cover a value the process never held — a key echoed by a library."""
    logger, stream = captured
    logger.info("upstream said: invalid key %s", CLAUDE_KEY)
    assert CLAUDE_KEY not in stream.getvalue()


def test_redaction_stops_when_the_credential_is_no_longer_in_use(captured):
    """The window is the call, not the process."""
    logger, stream = captured
    with redacting(TELEGRAM_TOKEN):
        pass
    logger.info("a later line mentioning %s", "nothing sensitive")
    assert MASK not in stream.getvalue()


def test_ordinary_log_lines_are_left_alone(captured):
    """Over-redaction is its own outage: a log an operator cannot read during an incident."""
    logger, stream = captured
    with redacting(CLAUDE_KEY):
        logger.info("harvest: 193 links, 12 new, 0 failures")
    assert "harvest: 193 links, 12 new, 0 failures" in stream.getvalue()


def test_short_values_are_never_registered():
    """Redacting a 4-character string globally would corrupt unrelated lines."""
    with redacting("abc"):
        assert redact("abc is a common substring") == "abc is a common substring"


# ══ 5. the storage layer, against a real database ══════════════════════════════════════════════

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def store_ctx():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield SecretStore(s, _ring()), s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _user(s) -> User:
    u = User(email=f"{uuid.uuid4()}@example.test", password_hash="x")
    s.add(u)
    s.flush()
    return u


def _secret_row(s, user_id, kind: str = "claude_api") -> UserSecret:
    return s.get(UserSecret, {"user_id": user_id, "kind": kind})


@pytestmark_db
def test_a_stored_credential_comes_back(store_ctx):
    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY)
    s.flush()

    with store.use(user.id, "claude_api") as key:
        assert key.reveal() == CLAUDE_KEY


@pytestmark_db
def test_nothing_the_database_holds_contains_the_plaintext(store_ctx):
    """T4's headline assertion, made the way an attacker would."""
    from sqlalchemy import text

    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY)
    store.put(user.id, "proxy", PROXY_URL)
    s.flush()

    dumped = ""
    for table in ("users", "user_secrets"):
        rows = s.execute(text(f"SELECT * FROM {table} WHERE "  # noqa: S608 - literal table names
                              f"{'id' if table == 'users' else 'user_id'} = :uid"),
                         {"uid": user.id}).mappings().all()
        dumped += repr([dict(r) for r in rows])

    assert CLAUDE_KEY not in dumped
    assert "s3cr3t-p4ssw0rd" not in dumped
    assert base64.b64encode(CLAUDE_KEY.encode()).decode() not in dumped


@pytestmark_db
def test_the_key_that_would_decrypt_it_is_not_in_the_database(store_ctx):
    """The other half."""
    from sqlalchemy import text

    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY)
    s.flush()

    stranger = SecretStore(s, _ring())
    with pytest.raises((DecryptionFailed, KeyUnavailable)):
        stranger.reveal(user.id, "claude_api")

    assert s.execute(text("SELECT wrapped_dek FROM users WHERE id = :uid"),
                     {"uid": user.id}).scalar() is not None, \
        "the wrapped DEK is in the database; the key that opens it is not"


@pytestmark_db
def test_one_user_cannot_read_another_users_credential(store_ctx):
    """T1's isolation claim, at the layer where getting it wrong is worst."""
    store, s = store_ctx
    alice, mallory = _user(s), _user(s)
    store.put(alice.id, "claude_api", CLAUDE_KEY)
    store.put(mallory.id, "claude_api", "sk-ant-api03-" + "Qq4" * 20)
    s.flush()

    assert store.reveal(mallory.id, "claude_api").reveal() != CLAUDE_KEY

    stolen = _secret_row(s, alice.id)
    victim = _secret_row(s, mallory.id)
    victim.ciphertext, victim.nonce = stolen.ciphertext, stolen.nonce
    s.flush()

    with pytest.raises(DecryptionFailed):
        store.reveal(mallory.id, "claude_api")


@pytestmark_db
def test_the_api_read_path_returns_metadata_and_never_a_value(store_ctx):
    """"Write-only through the API" made concrete: what the dashboard calls, and what it gets."""
    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY)
    s.flush()

    (row,) = TenantRepository(s).secret_metadata(user.id)
    assert row.kind == "claude_api"
    assert row.last_4 == CLAUDE_KEY[-4:]
    assert CLAUDE_KEY not in repr(row)

    names = {f.name for f in dataclasses.fields(row)}
    assert not (names & {"ciphertext", "value", "secret", "plaintext", "nonce"}), \
        "the metadata object must have no field that could ever carry the secret"


@pytestmark_db
def test_rotation_replaces_and_the_old_credential_is_gone(store_ctx):
    """"`PUT` replaces; old ciphertext deleted."""
    from sqlalchemy import text

    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY)
    s.flush()

    replacement = "sk-ant-api03-" + "Nn7" * 20
    store.put(user.id, "claude_api", replacement)
    s.flush()

    assert store.reveal(user.id, "claude_api").reveal() == replacement
    assert s.execute(text("SELECT count(*) FROM user_secrets WHERE user_id = :uid"),
                     {"uid": user.id}).scalar() == 1


@pytestmark_db
def test_validation_state_is_recorded_not_assumed(store_ctx):
    """A credential is unvalidated until something proved it works."""
    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY)
    s.flush()
    assert _secret_row(s, user.id).validated_at is None

    assert store.mark_validated(user.id, "claude_api") is True
    s.flush()
    assert _secret_row(s, user.id).validated_at is not None


@pytestmark_db
def test_rotating_a_credential_clears_its_validation(store_ctx):
    """A new key has not been proven; carrying the old verdict forward would say it had."""
    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY, validated=True)
    s.flush()
    store.put(user.id, "claude_api", "sk-ant-api03-" + "Bb2" * 20)
    s.flush()
    assert _secret_row(s, user.id).validated_at is None


@pytestmark_db
def test_purge_destroys_the_key_as_well_as_the_rows(store_ctx):
    """Deleting rows is undone by restoring a backup."""
    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY)
    store.put(user.id, "telegram_bot", TELEGRAM_TOKEN)
    s.flush()

    assert store.purge(user.id) == 2
    s.flush()

    assert store.reveal(user.id, "claude_api") is None
    assert user.wrapped_dek is None


@pytestmark_db
def test_a_kek_rotation_rewraps_without_touching_the_secrets(store_ctx):
    """The point of the envelope."""
    _, s = store_ctx
    user = _user(s)

    old_entry = f"k1:{generate_key()}"
    before_rotation = SecretStore(s, KeyRing.from_env({"SECRETS_KEK": old_entry}))
    before_rotation.put(user.id, "claude_api", CLAUDE_KEY)
    s.flush()
    sealed = _secret_row(s, user.id).ciphertext

    rotated = SecretStore(
        s, KeyRing.from_env({"SECRETS_KEK": f"k2:{generate_key()},{old_entry}"}))
    assert user.id in rotated.users_needing_rewrap()
    assert rotated.rewrap(user.id) is True
    s.flush()

    assert _secret_row(s, user.id).ciphertext == sealed, "secrets are not re-encrypted"
    assert rotated.reveal(user.id, "claude_api").reveal() == CLAUDE_KEY
    assert rotated.rewrap(user.id) is False, "rewrap is idempotent"
    assert user.id not in rotated.users_needing_rewrap()


@pytestmark_db
def test_an_unknown_kind_is_refused(store_ctx):
    """The database has a CHECK constraint; this fails earlier and says which values are legal."""
    store, s = store_ctx
    user = _user(s)
    with pytest.raises(UnknownSecretKind):
        store.put(user.id, "openai_api", "sk-whatever-1234")


@pytestmark_db
def test_an_empty_credential_is_refused(store_ctx):
    """Stores fine, fails later as an auth error nobody traces back to the form."""
    store, s = store_ctx
    user = _user(s)
    with pytest.raises(ValueError):
        store.put(user.id, "claude_api", "   ")


@pytestmark_db
def test_asking_for_a_credential_that_was_never_set_is_not_an_error(store_ctx):
    """A user who has not connected Telegram yet is the normal case, not an exception."""
    store, s = store_ctx
    user = _user(s)
    assert store.reveal(user.id, "telegram_bot") is None
    with store.use(user.id, "telegram_bot") as key:
        assert key is None


@pytestmark_db
def test_a_secret_row_without_its_key_fails_loudly(store_ctx):
    """Only reachable through a partial restore."""
    store, s = store_ctx
    user = _user(s)
    store.put(user.id, "claude_api", CLAUDE_KEY)
    s.flush()
    user.wrapped_dek = None
    s.flush()

    with pytest.raises(SecretsError):
        store.reveal(user.id, "claude_api")
