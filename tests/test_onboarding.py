"""Onboarding: the completion gate, and the two pages this service owns."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")

from libs.contracts.onboarding import IdentitySchema

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


# ── the identity contract (no database) ───────────────────────────────────────────────────────

def test_a_pasted_url_without_a_scheme_is_repaired_rather_than_refused():
    """People paste `github.com/name` constantly."""
    identity = IdentitySchema(full_name="Alice Example", links=[{"url": "github.com/alice"}])
    assert identity.links[0].url == "https://github.com/alice"


def test_the_platform_is_inferred_from_the_host():
    """Asking a user to classify their own GitHub URL is a question with one possible answer."""
    identity = IdentitySchema(full_name="Alice Example", links=[
        {"url": "https://www.linkedin.com/in/alice"},
        {"url": "https://gist.github.com/alice"},
        {"url": "https://alice.dev"}])
    assert [link.platform for link in identity.links] == ["linkedin", "github", "other"]


def test_a_string_with_no_host_is_refused():
    """Repairing this would put a dead link on a CV sent to an employer."""
    with pytest.raises(ValueError, match="not a usable web address"):
        IdentitySchema(full_name="Alice Example", links=[{"url": "my portfolio"}])


def test_the_same_link_twice_is_stored_once():
    identity = IdentitySchema(full_name="Alice", links=[
        {"url": "https://github.com/alice"}, {"url": "https://github.com/alice/"}])
    assert len(identity.links) == 1


# ── the routes ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def api():
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    os.environ["DISABLE_RATE_LIMIT"] = "1"

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import User
    from services.api import deps
    from services.api.app import app

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    session.add(user)
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
            yield http, session, user
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


CONFIG = {"sections": [{"platform": "all", "keywords": ["backend engineer"],
                        "locations": ["europe"]}]}


def _finish_every_page(http, session, user):
    """Satisfy all seven pages by writing what each one writes, not by calling every endpoint."""
    from libs.db.models import UserDocument, UserSecret

    http.put("/onboarding/identity", json={"full_name": "Alice Example"})
    http.put("/onboarding/history", json={"text": "I have done things. " * 20})
    http.put("/onboarding/config", json=CONFIG)
    session.add(UserDocument(user_id=user.id, kind="cv", filename="cv.txt", text="Alice",
                             text_sha256=uuid.uuid4().hex * 2, chars=5))
    for kind in ("claude_api", "telegram_bot", "proxy"):
        session.add(UserSecret(user_id=user.id, kind=kind, ciphertext=b"x", nonce=b"n"))
    user.telegram_status = "connected"
    # A connected bot is not a finished Telegram setup: the job stream needs a channel to land in.
    user.telegram_routes = {"jobs": "-1001234567890"}
    session.flush()


@pytestmark_db
def test_a_new_account_is_sent_to_the_first_page(api):
    http, _, _ = api
    body = http.get("/onboarding/state").json()
    assert body["complete"] is False
    assert body["next_page"] == "identity"
    assert [page["key"] for page in body["pages"]] == [
        "identity", "documents", "history", "telegram", "claude", "proxy", "config"]


@pytestmark_db
def test_completing_a_page_advances_to_the_next_unfinished_one(api):
    http, session, _ = api
    http.put("/onboarding/identity", json={"full_name": "Alice Example"})
    session.flush()
    assert http.get("/onboarding/state").json()["next_page"] == "documents"


@pytestmark_db
def test_finishing_is_refused_until_every_page_is_done_and_says_which(api):
    """A gate that says "not ready" without saying what is missing sends the user hunting."""
    http, _, _ = api
    http.put("/onboarding/identity", json={"full_name": "Alice Example"})

    response = http.post("/onboarding/complete")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "identity" not in detail["missing"]
    assert {"documents", "history", "telegram", "claude", "proxy", "config"} <= set(
        detail["missing"])


@pytestmark_db
def test_finishing_does_not_start_the_notifier(api):
    """`active` means the scheduler may dispatch."""
    http, session, user = api
    _finish_every_page(http, session, user)

    assert http.post("/onboarding/complete").json()["status"] == "complete"
    session.flush()
    assert user.onboarding_completed_at is not None
    assert user.active is False


@pytestmark_db
def test_revoking_a_credential_reopens_its_page(api):
    """The reason completion is derived rather than stored."""
    from libs.db.models import UserSecret

    http, session, user = api
    _finish_every_page(http, session, user)
    http.post("/onboarding/complete")
    session.flush()

    session.delete(session.get(UserSecret, (user.id, "claude_api")))
    user.telegram_status = "unconfigured"
    session.flush()

    body = http.get("/onboarding/state").json()
    assert body["complete"] is False
    assert body["next_page"] == "telegram"


@pytestmark_db
def test_a_connected_bot_without_a_channel_does_not_finish_telegram(api):
    """Delivery is the product, so the channel is a requirement rather than a nicety."""
    http, session, user = api
    _finish_every_page(http, session, user)

    user.telegram_routes = None
    session.flush()

    body = http.get("/onboarding/state").json()
    assert body["complete"] is False
    assert body["next_page"] == "telegram"

    user.telegram_routes = {"jobs": "-1001234567890"}
    session.flush()

    assert http.get("/onboarding/state").json()["complete"] is True


@pytestmark_db
def test_the_history_page_replaces_rather_than_accumulates(api):
    """It is one document the user edits."""
    from sqlalchemy import func, select

    from libs.db.models import UserDocument

    http, session, user = api
    http.put("/onboarding/history", json={"text": "First version. " * 20})
    http.put("/onboarding/history", json={"text": "Second version. " * 20})
    session.flush()

    assert session.scalar(select(func.count()).select_from(UserDocument)
                          .where(UserDocument.user_id == user.id)) == 1
    assert "Second version" in http.get("/onboarding/history").json()["text"]


@pytestmark_db
def test_a_config_whose_keywords_match_nothing_says_so_before_it_is_saved(api):
    """The failure this preview exists to catch: a misspelled keyword."""
    from libs.db.models import CorpusJob

    http, session, _ = api
    session.add(CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                          title="Senior Backend Engineer"))
    session.flush()

    matching = http.post("/onboarding/config/preview", json=CONFIG).json()
    assert matching["matches"] >= 1
    assert any("Backend Engineer" in title for title in matching["examples"])

    typo = {"sections": [{"platform": "all", "keywords": ["backedn enginer"]}]}
    assert http.post("/onboarding/config/preview", json=typo).json()["matches"] == 0


@pytestmark_db
def test_the_config_is_validated_server_side(api):
    """The form is the usability layer; a write path that bypassed it must not bypass the bounds."""
    http, _, _ = api
    over_cap = {"sections": [{"platform": "all", "keywords": [f"term {n}" for n in range(30)]}]}
    assert http.put("/onboarding/config", json=over_cap).status_code == 422


@pytestmark_db
def test_onboarding_requires_signing_in(api):
    http, _, _ = api
    http.cookies.clear()
    assert http.get("/onboarding/state").status_code == 401


# ── the schema payloads nobody reads (2026-08-11) ──────────────────────────────────────────────

def test_the_identity_and_config_routes_return_the_users_own_data(api):
    """**Written before removing the `schema` key from these payloads**, to pin what must survive.

    Three routes served a runtime JSON Schema with the docstring "the front end generates its forms
    from it". Nothing in `web/src` reads a runtime schema — the forms are hand-written TSX against
    the generated TypeScript types — so the payload was a second copy of every field list, kept in
    step by hand.

    What these routes are actually for is the account's own stored values. That is what this asserts,
    so the removal is provably a removal of the unused half.
    """
    http, _session, _user = api
    http.put("/onboarding/identity", json={"full_name": "Alice Example"})
    http.put("/onboarding/config", json=CONFIG)

    identity = http.get("/onboarding/identity").json()
    assert identity["full_name"] == "Alice Example"
    assert identity["links"] == []

    config = http.get("/onboarding/config").json()
    assert config["config"]["sections"][0]["keywords"] == CONFIG["sections"][0]["keywords"]


def test_saving_your_links_marks_the_profile_files_as_behind(api):
    """**The bug this closes, end to end through the real route.**

    An account added two links under Your details after setup had finished. `users.links` updated, the
    prompts that carry their contact line did not, and nothing anywhere said so — no error, no banner,
    and the CV kept describing the older answer.

    Asserted through the endpoint rather than by calling the stamp directly: the failure was that
    nothing on the save path set it, so a test that sets it itself would have passed throughout.
    """
    from libs.db.models import User

    http, session, user = api
    assert session.get(User, user.id).hard_data_changed_at is None

    http.put("/onboarding/identity",
             json={"full_name": "Alice Example",
                   "links": [{"url": "https://github.com/alice"},
                             {"url": "https://alice.dev"}]})
    session.flush()

    assert session.get(User, user.id).hard_data_changed_at is not None, (
        "saving links left nothing to compare the profile files against")


def test_saving_your_search_settings_marks_the_profile_files_as_behind(api):
    """`label.md` and the role angles were written against the previous keywords."""
    from libs.db.models import User

    http, session, user = api
    http.put("/onboarding/config", json=CONFIG)
    session.flush()

    assert session.get(User, user.id).hard_data_changed_at is not None
