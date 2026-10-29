"""Onboarding: the completion gate, the review it fills in from the CV, and its pages."""
# ruff: noqa: RUF001 - CVs are written with en dashes, and the fixtures are written like CVs
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

CV = """JANE DOE
Budapest, Hungary | jane.doe@example.com | github.com/janedoe
EXPERIENCE
Senior Backend Engineer, Acme Payments\tJan 2014 – Present
Designed the settlement service in Go
LANGUAGES
Hungarian (native), English (C1)
"""


def _upload_cv(http, text: str = CV):
    return http.post("/documents", data={"kind": "cv"},
                     files={"file": ("cv.txt", text.encode(), "text/plain")})


@pytestmark_db
def test_a_new_account_is_sent_to_the_first_of_three_screens(api):
    """Upload, confirm what we found, done (ROADMAP Phase 8). Nothing else stands in the way."""
    http, _, _ = api
    body = http.get("/onboarding/state").json()
    assert body["complete"] is False
    assert body["next_page"] == "documents"
    assert [page["key"] for page in body["pages"]] == ["documents", "review"]


@pytestmark_db
def test_uploading_a_cv_moves_them_on_to_the_review(api):
    http, _, _ = api
    _upload_cv(http)
    assert http.get("/onboarding/state").json()["next_page"] == "review"


@pytestmark_db
def test_the_review_arrives_filled_in_from_their_cv(api):
    """Confirm what we found, rather than type it: every field here was read off the CV."""
    http, _, _ = api
    _upload_cv(http)

    review = http.get("/onboarding/review").json()

    assert review["identity"] == {"full_name": "Jane Doe", "links": [
        {"url": "https://github.com/janedoe", "platform": "github"}]}
    section = review["config"]["sections"][0]
    assert section["keywords"] == ["backend engineer"]
    assert {"junior", "unpaid"} <= set(section["exclude_keywords"])
    assert section["locations"] == ["budapest"]
    assert review["config"]["languages"] == ["hu", "en"]
    assert review["found"]["titles"] == ["Senior Backend Engineer"]
    assert review["found"]["email"] == "jane.doe@example.com"
    assert {"term": "backend engineer", "reason": "Your job title at Acme Payments"} in (
        review["suggestions"]["green"])
    # The same answer the two pages that edit it give, so the stacked forms open filled in too.
    assert http.get("/onboarding/identity").json() == review["identity"]
    assert http.get("/onboarding/config").json()["config"] == review["config"]


@pytestmark_db
def test_what_they_saved_wins_over_what_their_cv_says(api):
    http, _, _ = api
    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Jane Q. Doe", "links": []})
    http.put("/onboarding/config", json=CONFIG)

    review = http.get("/onboarding/review").json()
    assert review["identity"] == {"full_name": "Jane Q. Doe", "links": []}
    assert review["config"]["sections"][0]["locations"] == ["europe"]


@pytestmark_db
def test_the_pickers_are_served_every_place_language_and_policy_with_a_label(api):
    """**The bug this closes**: the location and language pickers were empty for two months.

    ConfigStep read `stored.schema`, which this route stopped sending on 2026-08-11, through a
    response type written by hand, so nothing failed and every list rendered empty.
    """
    from libs.contracts.config import Language, Location, RemotePolicy

    http, _, _ = api
    choices = http.get("/onboarding/config").json()["choices"]

    for key, enum in (("locations", Location), ("languages", Language),
                      ("remote_policies", RemotePolicy)):
        assert [choice["value"] for choice in choices[key]] == [member.value for member in enum]
        assert all(choice["label"] for choice in choices[key]), key
    assert {"value": "hu", "label": "Hungarian", "hint": None} in choices["languages"]
    assert {"value": "united_kingdom", "label": "United Kingdom", "hint": None} in choices["locations"]


@pytestmark_db
def test_the_place_limit_the_page_is_served_is_the_one_a_save_enforces(api):
    """The picker stops at the served number, so it must be the number that refuses a save — a copy
    of it in the page would agree today and drift tomorrow (CLAUDE.md §5)."""
    from libs.contracts.config import Location

    http, _, _ = api
    most = http.get("/onboarding/review").json()["choices"]["max_locations"]
    places = [place.value for place in Location]
    assert len(places) > most, "the limit can be reached, or it would not need serving"

    def save(count: int) -> int:
        return http.put("/onboarding/config", json={"sections": [{
            "platform": "all", "keywords": ["nurse"], "locations": places[:count]}]}).status_code

    assert save(most) == 200
    assert save(most + 1) == 422


@pytestmark_db
def test_finishing_needs_a_cv_a_name_and_one_green_word_and_nothing_else(api):
    """No Telegram, no Claude key, no proxy: none of them is needed to see a matching job."""
    from libs.db.models import UserSecret

    http, session, user = api
    response = http.post("/onboarding/complete")
    assert response.status_code == 409
    assert response.json()["detail"]["missing"] == ["documents", "review"]

    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Jane Doe"})
    http.put("/onboarding/config", json=CONFIG)

    assert http.post("/onboarding/complete").json() == {"status": "complete"}
    session.flush()
    assert user.onboarding_completed_at is not None
    assert session.query(UserSecret).filter_by(user_id=user.id).count() == 0
    assert user.telegram_status == "unconfigured"


@pytestmark_db
def test_finishing_does_not_start_the_notifier(api):
    """`active` means the scheduler may dispatch."""
    http, session, user = api
    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Alice Example"})
    http.put("/onboarding/config", json=CONFIG)

    assert http.post("/onboarding/complete").json()["status"] == "complete"
    session.flush()
    assert user.active is False


@pytestmark_db
def test_deleting_the_cv_reopens_its_page(api):
    """The reason completion is derived rather than stored."""
    http, _, _ = api
    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Alice Example"})
    http.put("/onboarding/config", json=CONFIG)
    assert http.get("/onboarding/state").json()["complete"] is True

    (document,) = http.get("/documents").json()["documents"]
    http.delete(f"/documents/{document['id']}")

    body = http.get("/onboarding/state").json()
    assert body["complete"] is False
    assert body["next_page"] == "documents"


@pytestmark_db
def test_the_about_you_text_replaces_rather_than_accumulates(api):
    """It is one document the user edits, and a sentence is enough."""
    from sqlalchemy import func, select

    from libs.db.models import UserDocument

    http, session, user = api
    http.put("/onboarding/history", json={"text": "First version. " * 20})
    assert http.put("/onboarding/history", json={"text": "Night shifts suit me."}).status_code == 200
    session.flush()

    assert session.scalar(select(func.count()).select_from(UserDocument)
                          .where(UserDocument.user_id == user.id)) == 1
    assert http.get("/onboarding/history").json()["text"] == "Night shifts suit me."


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

@pytestmark_db
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


@pytestmark_db
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


@pytestmark_db
def test_saving_your_search_settings_marks_the_profile_files_as_behind(api):
    """`label.md` and the role angles were written against the previous keywords."""
    from libs.db.models import User

    http, session, user = api
    http.put("/onboarding/config", json=CONFIG)
    session.flush()

    assert session.get(User, user.id).hard_data_changed_at is not None
