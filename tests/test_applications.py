"""What happened after "I applied", in the person's own words, and what it adds up to (owner,
2026-11-03): an interview, an offer, the job, or not this time, each a step that can be taken back."""
from __future__ import annotations

import os
import uuid

import pytest

from libs.db import insert, now
from libs.db.models import BuiltDocument, CorpusJob, User
from libs.db.models.tenant import record_state
from tests.conftest import needs_db

pytestmark = needs_db


@pytest.fixture()
def api(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from services.api.app import app

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                           active=True, onboarding_completed_at=now()))
    job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test", title="Engineer"))
    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield http, db, user, job


def _stages(response) -> list[str]:
    return [step["stage"] for step in response.json()]


def test_an_application_moves_forward_ends_and_can_be_taken_back_a_step(api):
    http, _db, _user, job = api
    http.put(f"/jobs/{job.id}/applied")

    assert _stages(http.put(f"/jobs/{job.id}/progress", json={"stage": "interview"})) == ["interview"]
    assert _stages(http.put(f"/jobs/{job.id}/progress", json={"stage": "hired"})) == ["interview", "hired"], \
        "a step may be skipped: an offer accepted on the spot"
    ended = http.put(f"/jobs/{job.id}/progress", json={"stage": "rejected"})
    assert ended.status_code == 409 and "ended" in ended.json()["detail"]

    assert _stages(http.delete(f"/jobs/{job.id}/progress")) == ["interview"], "a mistaken tap, undone"
    ended = http.put(f"/jobs/{job.id}/progress", json={"stage": "rejected"})
    assert _stages(ended) == ["interview", "rejected"]

    (applied,) = http.get("/applied").json()
    assert [step["stage"] for step in applied["progress"]] == ["interview", "rejected"]
    assert all(step["at"] for step in applied["progress"]), "each step keeps when it was said"


def test_an_application_saved_before_steps_existed_takes_its_first_one(api):
    """Every row from before 2026-11-03 has no `progress` at all. The first answer on each was refused
    as "changed meanwhile" until the guard read a missing list as an empty one."""
    from libs.db.models import UserJobState

    http, db, user, job = api
    http.put(f"/jobs/{job.id}/applied")
    db[UserJobState.COLLECTION].update_one({"user_id": user.id, "job_id": job.id},
                                           {"$unset": {"progress": ""}})

    assert _stages(http.put(f"/jobs/{job.id}/progress", json={"stage": "interview"})) == ["interview"]
    assert _stages(http.put(f"/jobs/{job.id}/progress", json={"stage": "offer"})) == ["interview", "offer"]


def test_a_step_is_refused_before_applying_and_backwards(api):
    http, _db, _user, job = api
    assert http.put(f"/jobs/{job.id}/progress", json={"stage": "interview"}).status_code == 409

    http.put(f"/jobs/{job.id}/applied")
    http.put(f"/jobs/{job.id}/progress", json={"stage": "offer"})
    backwards = http.put(f"/jobs/{job.id}/progress", json={"stage": "interview"})
    assert backwards.status_code == 409 and "already at offer" in backwards.json()["detail"]
    assert http.put(f"/jobs/{job.id}/progress", json={"stage": "maybe"}).status_code == 422


def test_how_far_applications_got_is_counted_by_the_cv_that_went_with_them(api):
    """The feedback the owner asked for: do the CVs made here, and passing the ATS check, get people
    interviews? A stage counts every application that reached it or went past it."""
    from libs.tenant.applications import outcomes

    _http, db, user, _ = api
    other = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))

    def application(who, *stages, cv=None):
        job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                                   title="Engineer"))
        record_state(db, who.id, job.id, applied_at=now(),
                     progress=[{"stage": stage, "at": now()} for stage in stages])
        if cv:
            mode, ats_ok = cv
            insert(db, BuiltDocument(id=uuid.uuid4().int % 10**9, user_id=who.id, job_id=job.id, kind="cv",
                                     mode=mode, filename="cv.pdf", content=b"%PDF", size=4, saved=True,
                                     ats_ok=ats_ok))

    application(user, "interview", "offer", "hired", cv=("instant", True))
    application(user, "interview", "rejected", cv=("ai", False))
    application(other, "rejected")
    application(other)

    rows = {row["group"]: {k: v for k, v in row.items() if k != "group"} for row in outcomes(db)}
    assert rows["All applications"] == {"applied": 4, "interview": 2, "offer": 1, "hired": 1, "rejected": 2}
    assert rows["Instant CV"]["hired"] == 1 and rows["AI-written CV"]["interview"] == 1
    assert rows["No CV made here"] == {"applied": 2, "interview": 0, "offer": 0, "hired": 0, "rejected": 1}
    assert rows["CV passed the ATS check"]["offer"] == 1
    assert rows["CV flagged by the ATS check"] == {"applied": 1, "interview": 1, "offer": 0, "hired": 0,
                                                   "rejected": 1}


def test_only_an_admin_sees_the_outcomes(api):
    http, db, user, _ = api
    assert http.get("/admin/outcomes").status_code == 404, "the admin pages are not there for anybody else"
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"is_admin": True}})
    assert http.get("/admin/outcomes").status_code == 200
