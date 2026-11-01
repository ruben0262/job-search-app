"""Green and red word suggestions: what is offered, why, and when it is worked out again."""
# ruff: noqa: RUF001 - CVs are written with en dashes, and the fixtures are written like CVs
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, timedelta

import pytest

from libs.documents.read import read
from libs.suggestions import compute
from tests.conftest import needs_db

ENGINEER = """JANE DOE
Budapest, Hungary | jane.doe@example.com
EXPERIENCE
Senior Backend Engineer, Acme Payments\t{start} – Present
Designed the settlement service in Go
Backend Engineer, Globex\t2017 – 2020
Built REST APIs in Python and Django
SKILLS
Python, Go, Django, Haskell
"""


def _facts(start: str = "Jan 2021"):
    return read({"cv": ENGINEER.format(start=start)}, today=date(2026, 10, 28))


def _terms(found: dict, kind: str) -> list[str]:
    return [each["term"] for each in found[kind]]


def _suggest(facts=None, *, config=None, titles=(), matched=(), dismissed=()):
    facts = facts or _facts()
    return compute(facts, config=config, titles=list(titles), matched=list(matched),
                   text=ENGINEER, dismissed=set(dismissed))


# ── the rules ─────────────────────────────────────────────────────────────────────────────────

def test_green_is_the_titles_they_held_then_the_skills_job_titles_here_also_name():
    found = _suggest(titles=["Python Developer", "Senior Python Engineer", "Go Engineer", "Nurse"])

    assert _terms(found, "green") == ["backend engineer", "python", "go"]
    assert found["green"][0]["reason"] == "Your job title at Acme Payments"
    assert found["green"][1]["reason"] == ("One of your skills, and in 2 job titles here, like "
                                           "“Python Developer”")
    # Haskell is theirs, but no job title here names it: suggesting it would find nothing.
    assert "haskell" not in _terms(found, "green")


def test_no_suggestion_is_longer_than_two_words():
    """A board searches a phrase as one thing, so a long one finds almost nothing (owner, 2026-11-01).
    A title naming two jobs offers both; a long one is cut to the two words that are the job."""
    cv = ENGINEER.replace("Senior Backend Engineer, Acme Payments",
                          "Software Engineer & Developer, Acme Payments")
    cv = cv.replace("Backend Engineer, Globex", "Full Stack Software Engineer, Globex")
    found = compute(read({"cv": cv.format(start="Jan 2021")}, today=date(2026, 10, 28)), config=None,
                    titles=[], matched=["Team Coordinator And Co Lead Of Operations"] * 5, text=cv,
                    dismissed=set())

    green = _terms(found, "green")
    assert green[:2] == ["software engineer", "developer"]
    assert all(len(term.split()) <= 2 for term in green + _terms(found, "red")), found
    assert "software engineer & developer" not in green


def test_red_is_the_seniority_their_years_are_furthest_from():
    veteran = _suggest(_facts("Jan 2014"))
    newcomer = _suggest(read({"cv": "EXPERIENCE\nData Analyst, Initech\tJan 2025 – Present\n"},
                             today=date(2026, 10, 28)))
    middle = _suggest(read({"cv": "EXPERIENCE\nData Analyst, Initech\tJan 2022 – Present\n"},
                           today=date(2026, 10, 28)))

    assert {"intern", "internship", "graduate", "junior", "trainee"} <= set(_terms(veteran, "red"))
    assert "12 years of experience" in veteran["red"][0]["reason"]
    assert {"senior", "lead", "principal", "head of"} <= set(_terms(newcomer, "red"))
    assert not {"senior", "junior"} & set(_terms(middle, "red"))


def test_pay_that_is_not_pay_is_always_offered_as_red():
    reasons = {each["term"]: each["reason"] for each in _suggest()["red"]}
    assert reasons["unpaid"] == "Jobs that pay nothing"
    assert reasons["commission only"] == "Jobs that pay only when you sell"


def test_a_word_their_green_words_keep_dragging_in_from_jobs_they_never_did_is_red():
    """The example in the plan: "engineer" finds "Sales Engineer", and they have never sold anything."""
    found = _suggest(config={"sections": [{"keywords": ["engineer"]}]},
                     matched=["Sales Engineer", "Sales Engineer (m/w/d)", "Field Sales Engineer",
                              "Remote Engineer", "Remote Python Engineer", "Engineer (Remote)",
                              "Engineer, Berlin", "Python Engineer Berlin", "Berlin Platform Engineer",
                              "Python Engineer"])

    red = {each["term"]: each["reason"] for each in found["red"]}
    assert red["sales"] == ("In 3 of the 10 jobs your words find, like “Sales Engineer”, and nowhere "
                            "in your documents")
    # Three times each, as often as "sales": but in their CV, a place, or how the job is done, and
    # none of those says the job is not theirs.
    assert not {"python", "berlin", "remote"} & set(red)


def test_nothing_already_in_their_search_or_dismissed_is_offered():
    found = _suggest(config={"sections": [{"keywords": ["Backend Engineer"],
                                           "exclude_keywords": ["unpaid"]}]},
                     titles=["Python Developer", "Go Engineer"], dismissed={"python"})

    assert "backend engineer" not in _terms(found, "green")
    assert "python" not in _terms(found, "green")
    assert "go" in _terms(found, "green")
    assert "unpaid" not in _terms(found, "red")


def test_a_red_word_inside_one_of_their_green_words_is_never_offered():
    """Red "junior" beside green "junior developer" would hide every job they asked for."""
    found = _suggest(_facts("Jan 2014"), config={"sections": [{"keywords": ["junior developer"]}]})
    assert "junior" not in _terms(found, "red")
    assert "trainee" in _terms(found, "red")


# ── worked out again when what they are made from changes ─────────────────────────────────────

@pytest.fixture()
def api(db, monkeypatch):
    pytest.importorskip("fastapi")
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db import insert, now
    from libs.db.models import CorpusJob, User
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    moment = now()
    for minute, title in enumerate(["Sales Engineer", "Sales Engineer EMEA", "Field Sales Engineer",
                                    "Python Developer", "Backend Engineer"]):
        insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title=title,
                             description="", is_remote=True, posted_at=moment,
                             first_seen_at=moment - timedelta(minutes=minute)))

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user


def _upload(http, text: str, kind: str = "cv", name: str = "cv.txt"):
    return http.post("/documents", data={"kind": kind}, files={"file": (name, text.encode(), "text/plain")})


@needs_db
def test_uploading_a_cv_works_them_out_and_deleting_it_takes_them_away(api):
    http, _, _ = api
    assert _upload(http, ENGINEER.format(start="Jan 2021")).status_code == 201

    found = http.get("/suggestions").json()
    assert {"term": "backend engineer", "reason": "Your job title at Acme Payments"} in found["green"]
    assert "python" in _terms(found, "green")

    (document,) = http.get("/documents").json()["documents"]
    http.delete(f"/documents/{document['id']}")
    assert "backend engineer" not in _terms(http.get("/suggestions").json(), "green")


@needs_db
def test_saving_their_search_and_editing_their_about_you_text_work_them_out_again(api):
    http, _, _ = api
    _upload(http, ENGINEER.format(start="Jan 2021"))
    assert "sales" not in _terms(http.get("/suggestions").json(), "red")

    # Their green word now finds three sales jobs: "sales" is worth hiding.
    http.put("/onboarding/config", json={"sections": [{"platform": "all", "keywords": ["engineer"]}]})
    assert "sales" in _terms(http.get("/suggestions").json(), "red")

    # Until their own words say they sell: then it is not foreign to them at all.
    http.put("/onboarding/history", json={"text": "I also run sales demos for our biggest customers."})
    assert "sales" not in _terms(http.get("/suggestions").json(), "red")


@needs_db
def test_accepting_puts_the_word_in_their_search_and_it_stops_being_suggested(api):
    from libs.db import find_one
    from libs.db.models import UserConfig

    http, db, user = api
    _upload(http, ENGINEER.format(start="Jan 2021"))

    assert http.post("/suggestions/accept", json={"term": "unpaid", "kind": "red"}).status_code == 409

    found = http.post("/suggestions/accept", json={"term": "python", "kind": "green"}).json()
    assert "python" not in _terms(found, "green")
    http.post("/suggestions/accept", json={"term": "unpaid", "kind": "red"})

    section = find_one(db, UserConfig, {"user_id": user.id}).config["sections"][0]
    assert section["keywords"] == ["python"]
    assert section["exclude_keywords"] == ["unpaid"]


@needs_db
def test_a_dismissed_word_stays_dismissed_when_they_are_worked_out_again(api):
    http, _, _ = api
    _upload(http, ENGINEER.format(start="Jan 2021"))

    assert "python" not in _terms(http.post("/suggestions/dismiss", json={"term": "Python"}).json(),
                                  "green")
    _upload(http, "A cover letter. I like Python.", kind="cover_letter", name="cover.txt")
    assert "python" not in _terms(http.get("/suggestions").json(), "green")


@needs_db
def test_a_cv_the_reader_cannot_follow_does_not_cost_the_upload(api, monkeypatch, caplog):
    """Suggestions are a nicety; the CV is the one thing onboarding cannot do without."""
    import libs.suggestions

    def broken(_documents):
        raise RuntimeError("a CV nobody anticipated")

    http, _, _ = api
    monkeypatch.setattr(libs.suggestions, "read", broken)
    with caplog.at_level(logging.ERROR, logger="libs.suggestions"):
        response = _upload(http, ENGINEER.format(start="Jan 2021"))

    assert response.status_code == 201
    assert len(http.get("/documents").json()["documents"]) == 1
    assert "could not work them out" in caplog.text
