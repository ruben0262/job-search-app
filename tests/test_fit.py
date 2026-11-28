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


# ── a job they cannot take, and the roles a CV names (VERSION 2, owner 2026-11-16, night) ─────────────────

DENTAL_CV = """Amy Clarke
Trainee Dental Nurse    2024 - present
Bright Smile Dental, Leeds
- Chairside support for two dentists, sterilising instruments, decontamination
- Patient records, appointments and reception cover"""
DENTAL_NURSE = ("Dental Nurse", """We are looking for a qualified dental nurse to join our practice.
Requirements:
You must be GDC registered.
Experience of chairside support and decontamination.""")
WAREHOUSE_CV = """Jan Kowal
Warehouse Operative    2021 - present
Northern Foods, Leeds
- Picking and packing orders, loading lorries, stock counts, pallet wrapping"""
WAREHOUSE_JOB = """Picking and packing orders, loading and unloading lorries, stock counts.
Requirements:
"""
FINANCE_CV = """Priya Shah
Assistant Finance Manager    2020 - present
Harlow Logistics Ltd
- Month-end close, management accounts, budgeting and forecasting
"""
FINANCE_MANAGER = ("Finance Manager", """Lead month-end close, management accounts and budgeting.
Requirements:
ACA, ACCA or CIMA qualified.
Experience of month-end close and management accounts.""")


@needs_db
def test_a_job_needing_a_registration_they_lack_shows_at_most_four_and_holding_it_lifts_it(db):
    """The rubric's rule for a blocker: a trainee dental nurse is not the qualified, registered one the
    practice must hire, however alike the work. With the registration, it is her job."""
    without = _scores(db, fit.person({"cv": [DENTAL_CV]}, "Amy Clarke"), {"job": DENTAL_NURSE})["job"]
    db.fit_scratch.drop()
    registered = DENTAL_CV + "\nGDC registered dental nurse, GDC number 123456"
    with_it = _scores(db, fit.person({"cv": [registered]}, "Amy Clarke"), {"job": DENTAL_NURSE})["job"]
    assert fit.shown(without) <= 4 < fit.shown(with_it), (without, with_it)


@needs_db
def test_a_post_open_only_to_its_own_staff_shows_at_most_four(db):
    made = fit.person({"cv": [NURSE_CV]}, "Sinead Byrne")
    title, text = STAFF_NURSE
    internal = (title, text + "\nInternal applicants only.")
    scores = _scores(db, made, {"open": STAFF_NURSE, "internal": internal})
    assert fit.shown(scores["internal"]) <= 4 < fit.shown(scores["open"]), scores


@needs_db
def test_a_licence_the_employer_trains_for_never_caps_and_one_it_requires_does(db):
    """"We will train you" and "or willing to obtain" offer the licence; "essential" and "or equivalent" ask
    for it, or for what counts as it."""
    made = fit.person({"cv": [WAREHOUSE_CV]}, "Jan Kowal")
    lines = {"trained": "Counterbalance forklift licence: we will train you.",
             "willing": "A counterbalance forklift licence, or willing to obtain one.",
             "essential": "A counterbalance forklift licence is essential.",
             "equivalent": "Counterbalance forklift licence or equivalent."}
    postings = {name: ("Warehouse Operative", WAREHOUSE_JOB + line) for name, line in lines.items()}
    scores = _scores(db, made, postings)
    shown = {name: fit.shown(score) for name, score in scores.items()}
    assert shown["trained"] > 4 and shown["willing"] > 4, shown
    assert shown["essential"] <= 4 and shown["equivalent"] <= 4, shown


@needs_db
def test_part_qualified_in_one_of_the_alternatives_asked_is_not_capped(db):
    """"ACA, ACCA or CIMA qualified" is one requirement met by any of the three, and someone working towards
    one is not barred from it; someone with none of them is."""
    part = fit.person({"cv": [FINANCE_CV + "ACCA part-qualified, two papers to go"]}, "Priya Shah")
    none = fit.person({"cv": [FINANCE_CV + "AAT Level 4"]}, "Priya Shah")
    on_way = _scores(db, part, {"job": FINANCE_MANAGER})["job"]
    db.fit_scratch.drop()
    without = _scores(db, none, {"job": FINANCE_MANAGER})["job"]
    assert fit.shown(without) <= 4 < fit.shown(on_way), (without, on_way)


def test_a_cv_names_its_role_and_not_its_address_or_its_employer():
    """A German CV laid out as a table: the role is the line that names a job, not "Persönliche Daten" or an
    address in its first lines, and not the employer beside the role. The first lines were read as roles until
    2026-11-16, and a German electrician's list scored 3.1 points too low for it."""
    from libs.fit.text import words

    cv = """Lebenslauf
Persönliche Daten
Anschrift: Hauptstraße 12, 80331 München
Berufserfahrung
03/2019 - heute | Elektroniker für Betriebstechnik | Stadtwerke München GmbH
- Wartung und Instandhaltung von Schaltanlagen"""
    made = fit.person({"cv": [cv]}, "Lukas Bauer")
    roles = {word for word, weight in made["title"].items() if weight == 1.0}
    assert set(words("Elektroniker")) <= roles, roles
    assert not roles & set(words("Anschrift Persönliche Daten Stadtwerke München Hauptstraße")), roles


# ── the credentials the third held-out set found missing (VERSION 3, owner 2026-11-17) ────────────────────

def _barred_and_held(db, title: str, text: str, *, without: str, held: str, name: str) -> tuple[int, int]:
    """The score one CV shows on a posting, without the line naming a credential and with it."""
    lacking = _scores(db, fit.person({"cv": [without]}, name), {"job": (title, text)})["job"]
    db.fit_scratch.drop()
    having = _scores(db, fit.person({"cv": [without + "\n" + held]}, name), {"job": (title, text)})["job"]
    return fit.shown(lacking), fit.shown(having)


PHARMACY_POST = """We are looking for an experienced pharmacist to join our renal pharmacy team.
Requirements:
• Registered with the General Pharmaceutical Council
• Clinical pharmacy experience on hospital wards, medicines reconciliation and discharge"""


@needs_db
def test_a_pharmacy_technician_is_not_registered_as_a_pharmacist(db):
    """The GPhC registers both trades. A technician's registration is not what an "Advanced Pharmacist" post
    asks when it names the register alone; a pharmacist's is, even written as the register and a number."""
    pharmacist = """Hannah Cole
Clinical Pharmacist    2019 - present
Itchen Valley Hospitals
- Medicines reconciliation, discharge prescriptions and ward rounds on renal and medical wards"""
    technician = pharmacist.replace("Clinical Pharmacist", "Senior Pharmacy Technician")
    scores = {}
    for who, cv in (("pharmacist", pharmacist + "\nGPhC 2087654"),
                    ("technician", technician + "\nGPhC registered pharmacy technician, 2018")):
        scores[who] = fit.shown(_scores(db, fit.person({"cv": [cv]}, "Hannah Cole"),
                                        {"job": ("Advanced Pharmacist - Renal", PHARMACY_POST)})["job"])
        db.fit_scratch.drop()
    assert scores["technician"] <= 4 < scores["pharmacist"], scores


@needs_db
def test_the_aseptic_in_process_check_is_not_the_dispensary_one(db):
    text = """Requirements:
• Registered pharmacy technician with GPhC
• Qualified as a pre and in-process checker in technical services
• Aseptic manufacturing experience"""
    cv = """Amira Patel
Senior Pharmacy Technician    2018 - present
- Aseptic unit: preparing and checking chemotherapy and parenteral nutrition, cleanroom monitoring
GPhC registered pharmacy technician; accredited checking technician (ACPT)"""
    lacking, having = _barred_and_held(db, "Senior Pharmacy Technician - Aseptic Services", text, without=cv,
                                       held="Qualified pre and in-process checker, technical services, 2022",
                                       name="Amira Patel")
    assert lacking <= 4 < having, (lacking, having)


@needs_db
def test_a_regulated_title_requires_its_registration_wherever_the_posting_names_it(db):
    """"As a band 5 registered nurse, you will ..." says what a "Neonatal Staff Nurse" is, under no heading
    that says it is required."""
    text = """About the role
As a band 5 registered nurse, you will carry out evidence-based nursing assessment and care planning for
babies on our neonatal unit, working with families and the wider team."""
    cv = """Grace Mensah
Neonatal Healthcare Assistant    2021 - present
- Feeding, observations and family support on a neonatal unit, working with the nursing team"""
    lacking, having = _barred_and_held(db, "Neonatal Staff Nurse", text, without=cv,
                                       held="Registered nurse (NMC), 2024", name="Grace Mensah")
    assert lacking <= 4 < having, (lacking, having)


@needs_db
def test_mrcvs_after_a_vets_name_is_the_membership(db):
    text = """Requirements:
• Membership of the Royal College of Veterinary Surgeons
• Small animal consulting and routine surgery"""
    cv = """Tom Reid
Veterinary Surgeon    2020 - present
Riverside Vets
- Small animal consulting, routine surgery, out-of-hours rota"""
    lacking, having = _barred_and_held(db, "Veterinary Surgeon", text, without=cv, held="Tom Reid BVSc MRCVS",
                                       name="Tom Reid")
    assert lacking <= 4 < having, (lacking, having)


@needs_db
def test_acting_as_an_electrical_qualified_supervisor_needs_the_qualification(db):
    text = """The role
• Design, install and commission electrical systems for commercial customers
• Act as qualified supervisor (QS) for our electrical accreditations, including NICEIC"""
    cv = """Sam Hughes
Senior Electrical Engineer    2016 - present
- Design, install and commission electrical systems for commercial customers
- Electrical accreditations, testing, inspection and compliance
18th Edition, AM2"""
    lacking, having = _barred_and_held(db, "Principal Electrical Engineer", text, without=cv,
                                       held="NICEIC Qualified Supervisor since 2021", name="Sam Hughes")
    assert lacking <= 4 < having, (lacking, having)


@needs_db
def test_italys_pharmacists_register_is_required_where_it_is_asked(db):
    text = """Requisiti:
Laurea in farmacia e abilitazione alla professione di farmacista
Iscrizione all'Albo dei Farmacisti"""
    cv = """Giulia Rossi
Farmacista    2019 - oggi
Farmacia Centrale, Milano
- Dispensazione, consulenza ai pazienti, preparazioni galeniche"""
    lacking, having = _barred_and_held(db, "Farmacista", text, without=cv,
                                       held="Iscritta all'Albo dei Farmacisti di Milano", name="Giulia Rossi")
    assert lacking <= 4 < having, (lacking, having)


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


def test_every_panel_person_has_a_profile():
    """The people the score is judged on are read as the site reads anybody: each one `people.json` names has
    documents in the panel, and a profile made of them. Made once for them all, not once for each asked."""
    from scripts.cvlab.fit.model import profiles

    people = [name for name in json.loads((FIT / "people.json").read_text(encoding="utf-8"))
              if not name.startswith("_")]
    made = profiles()
    assert people and not [name for name in people if not (made.get(name) or {}).get("vector")]


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

    from libs.corpus.repository import CorpusRepository
    from libs.db import insert, now
    from libs.db.models import CorpusJob, User, UserConfig
    from services.api.app import app
    from tests.conftest import access_cookie

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
        http.cookies.set("access_token", access_cookie(db, user.id), path="/")
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
    """Kept beside the posting, which holds none of them: inside, they were 2,080 of its 2,910 bytes, held in
    the database's memory with every posting a list read (server_db, 2026-11-28)."""
    from time import monotonic

    from libs.db import insert, now
    from libs.db.models import CorpusJob

    _, db, _, jobs = site
    stored = db[CorpusJob.COLLECTION].find_one({"_id": jobs["backend"]})
    beside = fit.features(db, [jobs["backend"]])[jobs["backend"]]
    assert beside["th"] and beside["vh"] and stored["fit_version"] == fit.VERSION
    assert "fit" not in stored, "the posting holds its features itself"
    older = insert(db, CorpusJob(url="https://example.com/older", source="test", title="Backend Engineer",
                                 description=BACKEND_JOB[1], described_at=now(),
                                 fit={"th": [1], "tw": [1.0], "vh": [1], "vw": [1.0]}, fit_version=3))
    db[CorpusJob.COLLECTION].update_one({"_id": older.id}, {"$set": {"fit": {"vh": [1]}}})  # as 3 stored them
    assert fit.catch_up(db, deadline=monotonic() + 10) >= 1
    assert fit.features(db, [older.id])[older.id]["vh"] != [1], "read again by this version"
    assert "fit" not in db[CorpusJob.COLLECTION].find_one({"_id": older.id})


@needs_db
def test_the_deploy_moves_the_features_still_inside_postings_once_and_then_reads_none(db):
    """Postings given their features by `VERSION` 3 held them inside: the deploy moves them beside each, and
    asks again at every deploy after, so asking again has to read no posting at all."""
    from libs.db import insert
    from libs.db.models import CorpusJob

    features = fit.job("Backend Engineer", BACKEND_JOB[1], "en")
    old = [insert(db, CorpusJob(url=f"https://example.com/old-{n}", source="test", title="Engineer")).id
           for n in range(3)]
    for job_id in old:
        db[CorpusJob.COLLECTION].update_one({"_id": job_id}, {"$set": {"fit": features, "fit_version": 3}})
    without = insert(db, CorpusJob(url="https://example.com/without", source="test", title="Engineer")).id
    db[CorpusJob.COLLECTION].update_one({"_id": without}, {"$set": {"fit_version": 3}})

    assert fit.move_once(db) == 4
    assert fit.features(db, [*old, without]) == dict.fromkeys(old, features)
    stored = {row["_id"]: row for row in db[CorpusJob.COLLECTION].find({"_id": {"$in": [*old, without]}})}
    assert all("fit" not in row and row["fit_version"] == fit.VERSION for job_id, row in stored.items()
               if job_id in old)
    assert "fit_version" not in stored[without], "with none to move, it is left to catch_up"

    db.command("profile", 2)
    try:
        assert fit.move_once(db) == 0
        db.command("profile", 0)
        asked = list(db["system.profile"].find({"op": "query", "ns": f"{db.name}.{CorpusJob.COLLECTION}"}))
    finally:
        db.command("profile", 0)
        db.drop_collection("system.profile")  # the database refuses the next test's emptying of it
    assert asked, "it asked nothing"
    assert [op["docsExamined"] for op in asked] == [0] * len(asked), "it read postings to find none to move"


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
def test_a_job_shows_the_same_score_under_bookmarks_and_add_a_job_as_on_jobs(site):
    """Owner, 2026-11-16: the Bookmarks and Add a job tabs show "the same job postings" as Jobs, its match
    score included, worked out the same way; and with no plan for it, none there either."""
    from libs.db import now
    from libs.db.models import User
    from libs.db.models.tenant import record_state

    http, db, user, jobs = site
    _cv(db, user, BACKEND_CV)
    shown = _shown(http)
    assert all(1 <= score <= 10 for score in shown.values()), shown
    for job_id in jobs.values():
        assert http.put(f"/jobs/{job_id}/bookmarked").status_code == 200
        record_state(db, user.id, job_id, added_at=now())  # what bringing it in by its link leaves
    for tab in ("/jobs/bookmarked", "/jobs/added"):
        assert {job["id"]: job["fit"] for job in http.get(tab).json()["jobs"]} == shown, tab

    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"subscription_status": None}})
    for tab in ("/jobs/bookmarked", "/jobs/added"):
        listed = http.get(tab).json()["jobs"]
        assert {job["fit"] for job in listed} == {None}, f"a free account has none on {tab}"


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


def test_a_line_that_is_one_endless_run_of_spaces_is_read_in_a_moment():
    """Cutting a line of a few words into its parts cost the square of the spaces between them: 20,000 took
    2.4 s, and a file at the size limit nine minutes, on the request that next opened their Jobs page
    (2026-11-20). Seconds are allowed here where it takes a fraction of one and took ten."""
    import time

    from libs.fit import person

    started = time.perf_counter()
    person({"cv": ["Jane Doe\nStaff Nurse" + " " * 40_000 + "St. James's Hospital"]}, "Jane Doe")
    assert time.perf_counter() - started < 3


@needs_db
def test_finding_no_posting_behind_reads_no_posting(db):
    """**A relationship, not a stopwatch.** After every harvest run the postings whose match features are
    behind are asked for, and almost always there are none. Finding none read every posting there is: 128 ms
    of 42,377 on the dev copy, and as much again for each as many more (2026-11-20). Asked of the database
    exactly as `catch_up` asks it, the question reads none of them."""
    import os
    from time import monotonic

    from pymongo import MongoClient, monitoring

    from libs.db import insert, now
    from libs.db.models import CorpusJob

    for at in range(30):
        insert(db, CorpusJob(url=f"https://example.com/{at}", source="test", title="Engineer",
                             description="We build things. " * 20, described_at=now(),
                             fit=fit.job("Engineer", "We build things.", "en"), fit_version=fit.VERSION))

    class Asked(monitoring.CommandListener):
        def __init__(self):
            self.finds = []

        def started(self, event):
            if event.command_name == "find" and event.command.get("find") == CorpusJob.COLLECTION:
                self.finds.append(dict(event.command))

        def succeeded(self, event): ...

        def failed(self, event): ...

    asked = Asked()
    with MongoClient(os.environ["MONGODB_URI"], uuidRepresentation="standard", tz_aware=True,
                     event_listeners=[asked]) as client:
        assert fit.catch_up(client[db.name], deadline=monotonic() + 30) == 0
    [question] = asked.finds
    again = {key: question[key] for key in ("find", "filter", "sort", "projection", "limit")
             if key in question}

    read = db.command("explain", again, verbosity="executionStats")["executionStats"]
    assert read["totalDocsExamined"] == 0, "it read postings to find that none is behind"
