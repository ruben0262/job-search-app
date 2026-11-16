"""The fit score (`libs.fit`): a person's own kind of job scores above one that shares only a word with
it, a changed document changes the score, the same text always reads the same, and the evaluation set it is
judged on is whole. Scored through the database expression, which is what a page would run."""
from __future__ import annotations

import json

import pytest

from libs import fit
from scripts.cvlab.fit.build import FIT, read_jsonl
from tests.conftest import needs_db

NURSE_CV = """Sinead Byrne, RGN
Staff Nurse, Surgical Ward (31 beds)                         September 2022 - present
St Aoife's University Hospital, Dublin
- Care for 6 patients on days and 8 on nights, from admission and pre-operative preparation to discharge.
- Post-operative care: pain assessment, PCA and epidural monitoring, wound drains, stoma and catheter care.
- Nurse in charge of the ward on nights; preceptor to student nurses; ward lead for the hand hygiene audit.
Staff Nurse, Medical Ward                                    2020 - 2022
Education: BSc General Nursing, 2020. Languages: English (native)"""
STAFF_NURSE = ("Staff Nurse - Surgical Ward", """We are recruiting a registered staff nurse for our 28-bed
surgical ward. You will provide pre-operative and post-operative care, manage wound care and drains, monitor
epidurals and PCA, and work with the ward sister on discharge planning. Requirements: active nursing
registration, acute hospital experience, a commitment to patient safety. Days and nights rota.""")
SOFTWARE = ("Senior Backend Engineer - Health Tech", """Our platform helps nurses schedule shifts. You will
design REST APIs in Python and Go, run PostgreSQL and Kafka on Kubernetes, and own services end to end.
Requirements: six years of backend engineering, distributed systems, cloud infrastructure, on call.""")


def _scores(db, made: dict, postings: dict[str, tuple[str, str]]) -> dict[str, float]:
    db.fit_scratch.insert_many([{"_id": name, "fit": fit.job(title, text, "en")}
                                for name, (title, text) in postings.items()])
    rows = db.fit_scratch.aggregate([{"$project": {"fit": fit.expression(made)}}])
    return {row["_id"]: row["fit"] for row in rows}


@needs_db
def test_their_own_kind_of_job_scores_above_one_that_only_mentions_their_word(db):
    """A software job that mentions nurses is in a nurse's list (her word is in it); its score must say it is
    not her job. Asserted as numbers on the scale, not only as an order."""
    made = fit.person({"cv": [NURSE_CV]}, "Sinead Byrne")
    scores = _scores(db, made, {"nurse": STAFF_NURSE, "software": SOFTWARE})
    assert 1 <= scores["software"] < scores["nurse"] <= 10.1
    assert round(scores["nurse"]) >= 8 and scores["nurse"] - scores["software"] >= 4, scores
    # Readers say 1. The first fitted model showed it as 5 and the model of 2026-11-16's evening as 2 (2.4):
    # the bottom of the scale is where the score is least sure (ROADMAP 52).
    assert round(scores["software"]) <= 3, scores


@needs_db
def test_a_changed_profile_changes_the_score(db):
    """What they add about themselves counts: a CV with nothing of the posting's work, then the same CV and an
    About you that has it."""
    posting = ("Warehouse Operative - Forklift", """Picking and packing orders, loading and unloading lorries,
    moving pallets with a counterbalance forklift. A forklift licence is essential. Shifts, weekends.""")
    thin = {"cv": ["Jan Kowal\nShop assistant, Corner Shop, Leeds  2021 - present\nTills, stock, customers"]}
    fuller = {**thin, "history": ["I have a counterbalance forklift licence and I worked two years picking, "
                                  "packing and loading lorries in a warehouse before the shop."]}
    before = _scores(db, fit.person(thin, "Jan Kowal"), {"job": posting})["job"]
    db.fit_scratch.drop()
    after = _scores(db, fit.person(fuller, "Jan Kowal"), {"job": posting})["job"]
    assert after > before + 0.5, (before, after)


@needs_db
def test_the_filter_and_the_number_on_a_card_agree_at_every_score(db):
    """A job is in "Match N+" exactly when its card shows N or more: just under a half, at it and just over,
    whether its score was kept or is worked out there and then. Scores run from about 1 to 10 (`model.json`'s
    steps, 1.0 to 10.0, and a thousandth of the sum)."""
    made = fit.person({"cv": [NURSE_CV]}, "Sinead Byrne")
    near = [0.75 + step * 0.25 + nudge for step in range(39) for nudge in (-0.001, 0, 0.001)]
    kept = {at: round(score, 3) for at, score in enumerate(near)}
    worked_out = _scores(db, made, {"nurse": STAFF_NURSE, "software": SOFTWARE})
    db.fit_scratch.insert_many([{"_id": at} for at in kept])
    for at_least in range(1, 11):
        found = {row["_id"] for row in db.fit_scratch.find(fit.at_least(made, kept, at_least))}
        wanted = {at for at, score in {**kept, **worked_out}.items() if fit.shown(score) >= at_least}
        assert found == wanted, at_least


def test_the_same_text_always_reads_the_same():
    """Positions are a hash that does not depend on the process (`zlib.crc32`, not `hash`), and a posting's
    features are a function of its text alone."""
    assert fit.position("nurs") == fit.position("nurs") == 3685
    assert fit.job(*STAFF_NURSE, "en") == fit.job(*STAFF_NURSE, "en")
    assert fit.person({"cv": [NURSE_CV]}) == fit.person({"cv": [NURSE_CV]})


def test_nothing_to_read_is_no_profile():
    assert fit.person({}) is None
    assert fit.person({"cv": [""]}) is None


def test_the_shipped_model_turns_a_higher_sum_into_a_number_no_lower():
    model = fit.model()
    assert set(model["weights"]) == {*fit.SIGNALS, "const"}
    bounds = [upper for upper, _ in model["steps"][:-1]]
    values = [value for _, value in model["steps"]]
    assert bounds == sorted(bounds) and values == sorted(values)
    assert values[0] >= 1 and values[-1] <= 10


def test_the_evaluation_set_is_whole():
    """Every pair drawn has two readings and a label on the scale; a pair read three or more apart has its
    third reading; every posting a pair names is frozen with its text."""
    pairs = read_jsonl(FIT / "pairs.jsonl")
    postings = {row["id"] for row in read_jsonl(FIT / "postings.jsonl")}
    labelled = {(row["person"], row["job"]): row for row in read_jsonl(FIT / "labels.jsonl")}
    assert {pair["job"] for pair in pairs} <= postings
    for pair in pairs:
        row = labelled.get((pair["person"], pair["job"]))
        if row is None:
            continue  # drawn, waiting for its readers
        assert 1 <= row["label"] <= 10
        if abs(row["a"] - row["b"]) >= 3:
            assert row["third"] is not None, row
    assert len(labelled) >= 490


@pytest.mark.parametrize("name", sorted(json.loads((FIT / "people.json").read_text(encoding="utf-8")))[1:4])
def test_a_panel_person_has_a_profile(name):
    from scripts.cvlab.fit.model import profiles

    assert profiles()[name]["vector"], name


# ── on the site: the Jobs page's cards and filter (owner, 2026-11-16: Starter and Member) ──────────────────

BACKEND_CV = """Jordan Lee
Backend Engineer                                           2021 - present
Fernbrook Payments, Berlin
- Build payment APIs in Python and Go on PostgreSQL; Kafka for events; services on Kubernetes.
- On call for the ledger service; cut p95 latency of settlement queries from 900 ms to 120 ms.
Junior Developer                                           2019 - 2021
Skills: Python, Go, PostgreSQL, Kafka, Kubernetes, REST APIs"""
BACKEND_JOB = ("Backend Engineer", """We are hiring a backend engineer to build payment APIs in Python and Go.
You will run PostgreSQL and Kafka on Kubernetes, own services end to end and join the on-call rota.
Requirements: three years of backend engineering, PostgreSQL, REST APIs, distributed systems.""")
SALES_JOB = ("Sales Development Representative", """Book meetings for our backend engineer recruiting
platform. You will call and email hiring managers, qualify leads in Salesforce and hand them to account
executives. Requirements: a year of outbound sales, a target-driven mindset, excellent phone manner.""")


@pytest.fixture()
def site(db, monkeypatch):
    import os
    import uuid

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.corpus.repository import CorpusRepository
    from libs.db import insert, now
    from libs.db.models import CorpusJob, User, UserConfig
    from services.api.app import app

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x",
                           subscription_status="active", status="active", active=True, full_name="Jordan Lee",
                           onboarding_completed_at=now()))
    insert(db, UserConfig(user_id=user.id, schema_version=2, config={
        "sections": [{"platform": "all", "keywords": ["backend engineer"]}], "languages": ["en"]}))
    jobs = {}
    for name, (title, text) in {"backend": BACKEND_JOB, "sales": SALES_JOB}.items():
        job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title=title))
        CorpusRepository(db).set_description(job.id, text)  # the producer: features with the text
        jobs[name] = job.id
    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user, jobs


def _cv(db, user, text: str) -> None:
    """A CV as an upload leaves it: the document, and the stamp every document change makes."""
    import hashlib

    from libs.db import insert, now
    from libs.db.models import User, UserDocument

    db[UserDocument.COLLECTION].delete_many({"user_id": user.id, "kind": "cv"})
    insert(db, UserDocument(user_id=user.id, kind="cv", filename="cv.txt", text=text, chars=len(text),
                            text_sha256=hashlib.sha256(text.encode()).hexdigest()))
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"documents_changed_at": now()}})


def _shown(http) -> dict[int, int | None]:
    return {job["id"]: job["fit"] for job in http.get("/feed?since_hours=24").json()["jobs"]}


@needs_db
def test_a_posting_gets_its_features_with_its_text_and_older_ones_from_the_catch_up(site):
    from time import monotonic

    from libs.db import insert, now
    from libs.db.models import CorpusJob

    _, db, _, jobs = site
    stored = db[CorpusJob.COLLECTION].find_one({"_id": jobs["backend"]})
    assert stored["fit"]["th"] and stored["fit"]["vh"] and stored["fit_version"] == fit.VERSION
    older = insert(db, CorpusJob(url="https://example.com/older", source="test", title="Backend Engineer",
                                 description=BACKEND_JOB[1], described_at=now()))
    assert fit.catch_up(db, deadline=monotonic() + 10) >= 1
    assert db[CorpusJob.COLLECTION].find_one({"_id": older.id})["fit"]["vh"]


@needs_db
def test_a_plan_with_match_scores_sees_one_on_every_job_its_own_trade_highest(site):
    http, db, user, jobs = site
    _cv(db, user, BACKEND_CV)
    scores = _shown(http)
    assert set(scores) == set(jobs.values()), "both have the word, so both are listed"
    assert all(1 <= score <= 10 for score in scores.values()), scores
    assert scores[jobs["backend"]] > scores[jobs["sales"]], scores


def _listed(http, at_least: int | None = None) -> set[int]:
    asked = "" if at_least is None else f"&min_fit={at_least}"
    return {job["id"] for job in http.get(f"/feed?since_hours=24{asked}").json()["jobs"]}


@needs_db
def test_the_filter_keeps_a_job_exactly_when_its_card_shows_that_or_more_kept_or_not(site):
    """"Match 7+" lists the jobs whose card says 7 or more, and only those: the first time, with nothing kept
    for them yet, and once the page that asked has kept their list's scores."""
    from libs.db.models import FitScore

    http, db, user, jobs = site
    _cv(db, user, BACKEND_CV)
    shown = _shown(http)
    assert shown[jobs["backend"]] > shown[jobs["sales"]], shown
    for kept in (False, True):
        if not kept:
            db[FitScore.COLLECTION].delete_many({"user_id": user.id})
        for at_least in range(1, 11):
            assert _listed(http, at_least) == {job for job, score in shown.items() if score >= at_least}, (
                at_least, kept, shown)
        assert db[FitScore.COLLECTION].count_documents({"user_id": user.id}) == len(jobs), (
            "a filtered page keeps the scores of their list for the next one")


@needs_db
def test_a_new_cv_changes_what_the_filter_keeps_at_once(site):
    """Scores kept for the CV before are not the answer once they upload another: the very next page filters
    by the new one."""
    http, db, user, jobs = site
    _cv(db, user, "Casey Lee\nSales Development Representative   2022 - present\nOutbound calls, Salesforce, "
                  "booked meetings for account executives, qualified leads, hit target every quarter")
    as_sales = _shown(http)
    assert _listed(http, as_sales[jobs["sales"]]) == {jobs["sales"]}, as_sales
    _cv(db, user, BACKEND_CV)
    as_backend = _shown(http)
    assert _listed(http, as_backend[jobs["backend"]]) == {jobs["backend"]}, as_backend


@needs_db
def test_a_new_name_or_link_scores_nothing_again(site):
    """The owner (2026-11-16): their name and links do not change a score, so changing them is not a reason
    to score their list again."""
    from libs.db.models import FitScore

    http, db, user, _ = site
    _cv(db, user, BACKEND_CV)
    before = _shown(http)
    kept = {row["job_id"]: row["key"] for row in db[FitScore.COLLECTION].find({"user_id": user.id})}
    assert kept, "the first page keeps their list's scores"
    saved = http.put("/onboarding/identity", json={"full_name": "Sam Okafor",
                                                   "links": [{"url": "https://github.com/jordanlee"}]})
    assert saved.status_code == 200, saved.text
    assert _shown(http) == before
    assert {row["job_id"]: row["key"] for row in db[FitScore.COLLECTION].find({"user_id": user.id})} == kept


@needs_db
def test_a_kept_score_goes_on_the_day_its_job_is_archived(site):
    """The owner (2026-11-16): kept as long as jobs stay before they are archived. A posting is archived
    `corpus_retention_days` after it was found (`libs.corpus.archive`); its scores go then too."""
    from datetime import timedelta

    from libs.db.models import CorpusJob, FitScore
    from libs.settings import corpus_retention_days, put

    http, db, user, jobs = site
    put(db, "corpus_retention_days", 12, by="test")
    _cv(db, user, BACKEND_CV)
    _shown(http)
    found = {row["_id"]: row["first_seen_at"] for row in db[CorpusJob.COLLECTION].find(
        {"_id": {"$in": list(jobs.values())}}, {"first_seen_at": 1})}
    kept = {row["job_id"]: row["expires_at"] for row in db[FitScore.COLLECTION].find({"user_id": user.id})}
    assert kept and kept == {job: found[job] + timedelta(days=corpus_retention_days(db)) for job in kept}


@needs_db
def test_a_keeping_that_fails_leaves_nothing_that_stops_the_next(site, monkeypatch):
    """A run that fails is logged and let go: the next page keeps their scores, with no time to wait out."""
    from libs.db.models import FitScore

    http, db, user, jobs = site
    _cv(db, user, BACKEND_CV)
    real = fit.kept
    calls = []

    def failing_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("the database went away")
        return real(*args)

    monkeypatch.setattr(fit, "kept", failing_once)
    _shown(http)
    assert calls and not db[FitScore.COLLECTION].count_documents({"user_id": user.id}), "the first run failed"
    _shown(http)
    assert db[FitScore.COLLECTION].count_documents({"user_id": user.id}) == len(jobs)


@needs_db
def test_starter_has_match_scores_too(site):
    from datetime import timedelta

    from libs.db import now
    from libs.db.models import CorpusJob, User

    http, db, user, jobs = site
    _cv(db, user, BACKEND_CV)
    # Starter sees a job once it is three days old: these were found and read four days ago.
    old = now() - timedelta(days=4)
    db[CorpusJob.COLLECTION].update_many({"_id": {"$in": list(jobs.values())}}, {"$set": {
        "first_seen_at": old, "described_at": old, "posted_at": old}})
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"subscription_plan": "starter"}})
    feed = http.get("/feed").json()
    assert feed["match_scores"] and {job["id"] for job in feed["jobs"]} == set(jobs.values())
    assert all(1 <= job["fit"] <= 10 for job in feed["jobs"]), feed["jobs"]


@needs_db
def test_no_score_without_match_scores_or_without_documents(site):
    from datetime import timedelta

    from libs.db import now
    from libs.db.models import CorpusJob, User

    http, db, user, jobs = site
    assert set(_shown(http).values()) == {None}, "a CV to compare with is needed"
    assert not http.get("/feed").json()["match_scores"]
    _cv(db, user, BACKEND_CV)
    # A free account sees a job once it is a week old: these were found and read eight days ago.
    week_old = now() - timedelta(days=8)
    db[CorpusJob.COLLECTION].update_many({"_id": {"$in": list(jobs.values())}}, {"$set": {
        "first_seen_at": week_old, "described_at": week_old, "posted_at": week_old}})
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"subscription_status": None}})
    feed = http.get("/feed?min_fit=9").json()
    listed = {job["id"]: job["fit"] for job in feed["jobs"]}
    assert listed and set(listed.values()) == {None}, "a free account has no match scores"
    assert set(listed) == set(jobs.values()) and not feed["match_scores"], "nor a filter by them"


@needs_db
def test_a_new_cv_changes_the_score_on_the_next_read(site):
    http, db, user, jobs = site
    _cv(db, user, "Casey Lee\nSales Development Representative   2022 - present\nOutbound calls, Salesforce, "
                  "booked meetings for account executives, qualified leads, hit target every quarter")
    as_sales = _shown(http)
    _cv(db, user, BACKEND_CV)
    as_backend = _shown(http)
    assert as_sales[jobs["sales"]] > as_sales[jobs["backend"]], as_sales
    assert as_backend[jobs["backend"]] > as_backend[jobs["sales"]], as_backend
