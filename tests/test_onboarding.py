"""Onboarding: the completion gate, the review it fills in from the CV, and its pages."""
# ruff: noqa: RUF001 - CVs are written with en dashes, and the fixtures are written like CVs
from __future__ import annotations

import json
import os
import pathlib
import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("fastapi")

from libs.contracts.onboarding import IdentitySchema
from tests.conftest import needs_db

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
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import User
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user


CONFIG = {"sections": [{"platform": "all", "keywords": ["backend engineer", "golang", "postgres"],
                        "locations": ["europe"]}], "languages": ["en"]}

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


@needs_db
def test_a_new_account_is_sent_to_the_first_of_three_screens(api):
    """Upload, confirm what we found, done (ROADMAP Phase 8). Nothing else stands in the way."""
    http, _, _ = api
    body = http.get("/dashboard").json()["onboarding"]
    assert body["complete"] is False
    assert body["next_page"] == "documents"
    assert [page["key"] for page in body["pages"]] == ["documents", "review"]


@needs_db
def test_uploading_a_cv_moves_them_on_to_the_review(api):
    http, _, _ = api
    _upload_cv(http)
    assert http.get("/dashboard").json()["onboarding"]["next_page"] == "review"


@needs_db
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
    assert section["locations"] == ["hungary"], "the city on the CV is offered as its country"
    assert review["config"]["languages"] == ["hu", "en"]
    assert review["found"]["titles"] == ["Senior Backend Engineer"]
    assert review["found"]["email"] == "jane.doe@example.com"
    assert {"term": "backend engineer", "reason": "Your job title at Acme Payments"} in (
        review["suggestions"]["green"])
    # The same answer the page that edits it gives, so the stacked forms open filled in too.
    assert http.get("/onboarding/identity").json() == review["identity"]


@needs_db
def test_what_they_saved_wins_over_what_their_cv_says(api):
    http, _, _ = api
    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Jane Q. Doe", "links": []})
    http.put("/onboarding/config", json=CONFIG)

    review = http.get("/onboarding/review").json()
    assert review["identity"] == {"full_name": "Jane Q. Doe", "links": []}
    assert review["config"]["sections"][0]["locations"] == ["europe"]


@needs_db
def test_the_pickers_are_served_every_place_language_and_policy_with_a_label(api):
    """**The bug this closes**: the location and language pickers were empty for two months.

    ConfigStep read `stored.schema`, which this route stopped sending on 2026-08-11, through a
    response type written by hand, so nothing failed and every list rendered empty.
    """
    from libs.contracts.config import Language, Location, Workplace

    http, _, _ = api
    choices = http.get("/onboarding/review").json()["choices"]

    for key, enum in (("locations", Location), ("languages", Language),
                      ("workplaces", Workplace)):
        assert [choice["value"] for choice in choices[key]] == [member.value for member in enum]
        assert all(choice["label"] for choice in choices[key]), key
    assert {"value": "hu", "label": "Hungarian", "hint": None} in choices["languages"]
    # Countries only (owner, 2026-11-02), the three most picked first.
    assert [choice["label"] for choice in choices["locations"][:3]] == [
        "European Union", "United Kingdom", "Ireland"]
    assert "berlin" not in [choice["value"] for choice in choices["locations"]]


@needs_db
def test_the_place_limit_the_page_is_served_is_the_one_a_save_enforces(api):
    """The picker stops at the served number, so it must be the number that refuses a save — a copy
    of it in the page would agree today and drift tomorrow (CLAUDE.md §5)."""
    from libs.contracts.config import Location

    http, _, _ = api
    most = http.get("/onboarding/review").json()["choices"]["max_locations"]
    places = [place.value for place in Location]
    assert len(places) > most, "the limit can be reached, or it would not need serving"

    def save(count: int) -> int:
        return http.put("/onboarding/config", json={**CONFIG, "sections": [{
            **CONFIG["sections"][0], "locations": places[:count]}]}).status_code

    assert save(most) == 200
    assert save(most + 1) == 422


@needs_db
def test_finishing_needs_a_cv_a_name_and_a_whole_search_and_nothing_else(api):
    """No Telegram, no Claude key, no proxy: none of them is needed to see a matching job."""
    from libs.db import get
    from libs.db.models import User, UserSecret

    http, db, user = api
    response = http.post("/onboarding/complete")
    assert response.status_code == 409
    assert response.json()["detail"]["missing"] == ["documents", "review"]

    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Jane Doe"})
    http.put("/onboarding/config", json=CONFIG)

    assert http.post("/onboarding/complete").json() == {"status": "complete"}
    stored = get(db, User, user.id)
    assert stored.onboarding_completed_at is not None
    assert db[UserSecret.COLLECTION].count_documents({"user_id": user.id}) == 0
    assert stored.telegram_status == "unconfigured"


@needs_db
@pytest.mark.parametrize(("change", "said"), [
    ({"keywords": ["backend engineer", "golang"]}, "add at least 3 green words"),
    ({"locations": []}, "pick at least one country"),
    ({"workplaces": []}, "switch on at least one way of working"),
    ({"languages": []}, "pick at least one language"),
])
def test_a_search_missing_a_part_is_not_saved_and_onboarding_does_not_finish(api, change, said):
    """At least three green words, one country, one way of working and one language (owner, 2026-11-04):
    jobs come in country by country, so a search with no country would have none."""
    http, _, _ = api
    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Jane Doe"})
    section, account = dict(CONFIG["sections"][0]), {"languages": CONFIG["languages"]}
    (account if "languages" in change else section).update(change)

    response = http.put("/onboarding/config", json={"sections": [section], **account})

    assert response.status_code == 422
    assert said in response.text
    assert http.post("/onboarding/complete").status_code == 409


@needs_db
def test_a_search_saved_before_the_minimums_reopens_its_page_even_once_finished(api):
    """Saved with one word and no country, and finished then: until it has the rest, the jobs page
    waits, and the page that asks for them opens."""
    from libs.db import insert, now
    from libs.db.models import User, UserConfig

    http, db, user = api
    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Jane Doe"})
    insert(db, UserConfig(user_id=user.id, config={"sections": [{"platform": "all", "keywords": ["nurse"]}]}))
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"onboarding_completed_at": now()}})

    body = http.get("/dashboard").json()
    assert (body["stage"], body["onboarding"]["next_page"]) == ("onboarding", "review")
    assert "3 green words, a country" in body["onboarding"]["pages"][1]["detail"]

    http.put("/onboarding/config", json=CONFIG)
    assert http.get("/dashboard").json()["stage"] == "ready"


@needs_db
def test_the_page_is_served_the_least_number_of_words_a_save_takes(api):
    from libs.contracts.config import MIN_GREEN_WORDS

    http, _, _ = api
    assert http.get("/onboarding/review").json()["choices"]["min_words"] == MIN_GREEN_WORDS == 3


@needs_db
def test_finishing_does_not_start_the_notifier(api):
    """`active` means the scheduler may dispatch."""
    from libs.db import get
    from libs.db.models import User

    http, db, user = api
    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Alice Example"})
    http.put("/onboarding/config", json=CONFIG)

    assert http.post("/onboarding/complete").json()["status"] == "complete"
    assert get(db, User, user.id).active is False


@needs_db
def test_deleting_the_cv_reopens_its_page(api):
    """The reason completion is derived rather than stored."""
    http, _, _ = api
    _upload_cv(http)
    http.put("/onboarding/identity", json={"full_name": "Alice Example"})
    http.put("/onboarding/config", json=CONFIG)
    assert http.get("/dashboard").json()["onboarding"]["complete"] is True

    (document,) = http.get("/documents").json()["documents"]
    http.delete(f"/documents/{document['id']}")

    body = http.get("/dashboard").json()["onboarding"]
    assert body["complete"] is False
    assert body["next_page"] == "documents"


@needs_db
def test_the_about_you_text_replaces_rather_than_accumulates(api):
    """It is one document the user edits, and a sentence is enough."""
    from libs.db.models import UserDocument

    http, db, user = api
    http.put("/onboarding/history", json={"text": "First version. " * 20})
    assert http.put("/onboarding/history", json={"text": "Night shifts suit me."}).status_code == 200

    assert db[UserDocument.COLLECTION].count_documents({"user_id": user.id}) == 1
    assert http.get("/onboarding/history").json()["text"] == "Night shifts suit me."


@needs_db
def test_the_config_is_validated_server_side(api):
    """The form is the usability layer; a write path that bypassed it must not bypass the bounds."""
    http, _, _ = api
    words = [f"term {n}" for n in range(30)]
    over_cap = {**CONFIG, "sections": [{**CONFIG["sections"][0], "keywords": words}]}
    assert http.put("/onboarding/config", json=over_cap).status_code == 422


@needs_db
def test_onboarding_requires_signing_in(api):
    http, _, _ = api
    http.cookies.clear()
    assert http.get("/onboarding/review").status_code == 401


# ── the schema payloads nobody reads (2026-08-11) ──────────────────────────────────────────────

@needs_db
def test_the_identity_and_config_routes_return_the_users_own_data(api):
    """**Written before removing the `schema` key from these payloads**, to pin what must survive.

    Three routes served a runtime JSON Schema with the docstring "the front end generates its forms
    from it". Nothing in `web/src` reads a runtime schema — the forms are hand-written TSX against
    the generated TypeScript types — so the payload was a second copy of every field list, kept in
    step by hand.

    What these routes are actually for is the account's own stored values. That is what this asserts,
    so the removal is provably a removal of the unused half.
    """
    http, _db, _user = api
    http.put("/onboarding/identity", json={"full_name": "Alice Example"})
    http.put("/onboarding/config", json=CONFIG)

    identity = http.get("/onboarding/identity").json()
    assert identity["full_name"] == "Alice Example"
    assert identity["links"] == []

    config = http.get("/onboarding/review").json()
    assert config["config"]["sections"][0]["keywords"] == CONFIG["sections"][0]["keywords"]


def test_words_in_a_section_my_search_never_showed_are_on_the_page_and_find_the_same_jobs(api):
    """A search stored in two sections, a LinkedIn-only one among them, from before (2026-11-06): its words
    found jobs, and My search showed and saved only the first section. Now the page shows them all, and
    saving what it shows finds the same jobs."""
    from libs.db import insert
    from libs.db.models import CorpusJob
    from libs.matching import matching_jobs
    from libs.tenant.repository import TenantRepository

    http, db, user = api
    TenantRepository(db).save_config(user.id, {"languages": ["en"], "sections": [
        {"platform": "all", "keywords": ["golang", "postgres", "kafka"], "locations": ["germany"]},
        {"platform": "linkedin", "keywords": ["rust"], "exclude_keywords": ["senior"],
         "locations": ["france"]}]}, schema_version=2)
    for title, place in (("Rust Engineer", "Paris, France"), ("Golang Developer", "Berlin, Germany"),
                         ("Senior Golang Developer", "Berlin, Germany"), ("Rust Engineer", "Madrid, Spain")):
        insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="greenhouse", title=title,
                             location=place, country={"France": "FR", "Germany": "DE", "Spain": "ES"}[
                                 place.split(", ")[1]], description="We build things. " * 20,
                             described_at=datetime.now(UTC)))
    before = sorted(match.job.title for match in matching_jobs(db, user))

    page = http.get("/onboarding/review").json()["config"]
    assert [section["keywords"] for section in page["sections"]] == [["golang", "postgres", "kafka", "rust"]]
    assert page["sections"][0]["exclude_keywords"] == ["senior"]
    assert page["sections"][0]["locations"] == ["germany", "france"]
    assert http.put("/onboarding/config", json=page).status_code == 200

    assert before == ["Golang Developer", "Rust Engineer"]
    assert sorted(match.job.title for match in matching_jobs(db, user)) == before


@needs_db
def test_saving_your_links_marks_the_profile_files_as_behind(api):
    """**The bug this closes, end to end through the real route.**

    An account added two links under Your details after setup had finished. `users.links` updated, the
    prompts that carry their contact line did not, and nothing anywhere said so — no error, no banner,
    and the CV kept describing the older answer.

    Asserted through the endpoint rather than by calling the stamp directly: the failure was that
    nothing on the save path set it, so a test that sets it itself would have passed throughout.
    """
    from libs.db import get
    from libs.db.models import User

    http, db, user = api
    assert get(db, User, user.id).hard_data_changed_at is None

    http.put("/onboarding/identity",
             json={"full_name": "Alice Example",
                   "links": [{"url": "https://github.com/alice"},
                             {"url": "https://alice.dev"}]})

    assert get(db, User, user.id).hard_data_changed_at is not None, (
        "saving links left nothing to compare the profile files against")


@needs_db
def test_saving_your_search_settings_marks_the_profile_files_as_behind(api):
    """`label.md` and the role angles were written against the previous keywords."""
    from libs.db import get
    from libs.db.models import User

    http, db, user = api
    assert get(db, User, user.id).hard_data_changed_at is None, "the premise"
    http.put("/onboarding/config", json=CONFIG)

    assert get(db, User, user.id).hard_data_changed_at is not None


CVS = pathlib.Path(__file__).parent / "fixtures" / "cvs"


@needs_db
@pytest.mark.parametrize("filename", sorted(json.loads((CVS / "expected.json").read_text(encoding="utf-8"))))
def test_a_cv_in_any_common_layout_fills_in_the_name_and_links(api, filename):
    """Word templates put the name in the page header, a layout table or a text box, and links behind
    the word "LinkedIn"; designed PDFs set the name over two lines, letter-spaced, beside the job title
    or under a sidebar. Uploaded in onboarding, every one fills in "What we found": in nine such
    files, three did before (2026-11-03). `make.py` beside them says how each was built."""
    expected = json.loads((CVS / "expected.json").read_text(encoding="utf-8"))[filename]
    http, _, _ = api
    uploaded = http.post("/documents", data={"kind": "cv"},
                         files={"file": (filename, (CVS / filename).read_bytes())})
    assert uploaded.status_code == 201, uploaded.text

    review = http.get("/onboarding/review").json()
    assert review["identity"]["full_name"] == expected["name"]
    assert [link["url"] for link in review["identity"]["links"]] == [
        url if "://" in url else f"https://{url}" for url in expected["links"]]
    assert (review["found"]["email"], review["found"]["phone"]) == (expected["email"], expected["phone"])
    offered = {each["term"] for each in review["suggestions"]["green"]}
    assert {"design engineer", "mechanical engineer"} <= offered, "its job titles, whatever the file"


@needs_db
@pytest.mark.parametrize("filename", ["letter.docx", "letter.pdf"])
def test_a_cover_letter_alone_suggests_the_job_titles_it_names_that_boards_use(api, filename):
    """A letter has no dated jobs or skills list: the roles are in its sentences. Each title boards
    here use (three postings or more) is offered; one too rare to search for is not."""
    from libs.db import insert
    from libs.db.models import CorpusJob

    http, db, _ = api
    boards = ["Product Designer", "Senior UX Designer", "Graphic Designer (m/w/d)"] * 3
    for title in [*boards, "Studio Manager"]:
        insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title=title))
    uploaded = http.post("/documents", data={"kind": "cover_letter"},
                         files={"file": (filename, (CVS / filename).read_bytes())})
    assert uploaded.status_code == 201, uploaded.text

    green = [each["term"] for each in http.get("/suggestions").json()["green"]]
    expected = json.loads((CVS / "letters.json").read_text(encoding="utf-8"))[filename]["titles"]
    assert set(expected) <= set(green), green
    assert "studio manager" not in green
