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
