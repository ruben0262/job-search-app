"""A CV or cover letter for a job: asked for on the site, built by the docgen worker, downloaded,
deleted. The real app and the real worker step share one rolled-back session; only Chrome and the AI
providers are stand-ins, and the providers are reached through their real adapters."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
import httpx

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
                                reason="DATABASE_URL not set (no live Postgres)")

PLATFORM_KEY = "AIzaPlatformKey-" + "p" * 24
OWN_KEY = "sk-proj-OwnKey-" + "o" * 30

CV_TEXT = """Alice Example
alice@example.com | Berlin, Germany

EXPERIENCE
Backend Engineer, Northwind    2021 - Present
- Built the billing service in Python and Postgres
- Cut checkout latency to 40ms at p99

EDUCATION
BSc Computer Science, TU Berlin    2017
"""

WRITTEN = """<!DOCTYPE html><html lang="en"><head><title>Alice Example CV</title></head><body>
<div class="cv-name">Alice Example</div><p class="profile">Backend engineer.</p>
<div class="section"><div class="section-title">Experience</div><ul><li>Built billing</li></ul></div>
</body></html>"""


@pytest.fixture()
def api(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.auth.access import issue_access_token
    from libs.db.models import CorpusJob, User, UserDocument
    from libs.secrets import KeyRing, SecretStore, generate_key
    from services.api import deps
    from services.api.app import app
    from services.docgen import pdf

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    kek = f"k1:{generate_key()}"
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("SECRETS_KEK", kek)
    monkeypatch.delenv("PLATFORM_AI_PROVIDER", raising=False)
    monkeypatch.delenv("PLATFORM_AI_KEY", raising=False)

    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                full_name="Alice Example")
    session.add(user)
    session.flush()
    session.add(UserDocument(user_id=user.id, kind="cv", filename="cv.txt", text=CV_TEXT,
                             text_sha256=hashlib.sha256(CV_TEXT.encode()).hexdigest(),
                             chars=len(CV_TEXT)))
    job = CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                    title="Backend Engineer", company="Northwind",
                    description="Backend engineer. Python, Postgres, on-call.")
    session.add(job)
    session.flush()

    # Chrome, as a function: what it was handed, and a PDF back.
    rendered: list[str] = []
    monkeypatch.setattr("services.docgen.run.pdf.render",
                        lambda html: rendered.append(html) or b"%PDF-1.4 test")
    monkeypatch.setattr("services.docgen.run.pdf.inspect_pdf",
                        lambda document, kind="cv": pdf.PdfReport(pages=1, chars=900, issues=[]))
    # The posting's freshness check fetches the posting; nothing here may touch the network.
    monkeypatch.setattr("services.docgen.run.refresh_before_build", lambda *a, **k: {})

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
            yield SimpleNamespace(http=http, session=session, user=user, job=job,
                                  rendered=rendered, store=SecretStore(session, KeyRing.from_env(
                                      {"SECRETS_KEK": kek})))
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()


def _ask(api, kind="cv", mode="instant"):
    return api.http.post("/documents/build", json={"job_id": api.job.id, "kind": kind, "mode": mode})


def _work(api) -> bool:
    """One step of the real docgen worker: claim, handle, complete."""
    from libs.queue.worker import WorkerStats, run_once
    from services.docgen.__main__ import handle

    return run_once(api.session, "docgen", "test-docgen", handle, WorkerStats())


def _status(api, build_id: int) -> dict:
    return api.http.get(f"/documents/build/{build_id}").json()


def _consent(api):
    version = api.http.get("/account/consent").json()["current_version"]
    assert api.http.post("/account/consent", json={"version": version}).status_code == 200


def _personalise(api):
    """What the setup chat left a legacy account with: the files the tailoring prompts need."""
    from libs.profiles.repository import ProfileFiles

    files = ProfileFiles(api.session, api.user.id)
    files.write("context.md", "# Alice Example\nBackend engineer in Berlin. Python, Postgres.",
                kind="context")
    files.write("base_cv.html", WRITTEN, kind="template")
    files.write("tailor_cv.md", "Tailor Alice Example's CV.\n<JOB_CONTEXT>\n<BASE_TEMPLATE>",
                kind="prompt")
    files.write("tailor_cover.md", "Write Alice Example a letter.\n<HEAD_BLOCK>\n<JOB_CONTEXT>",
                kind="prompt")
    api.session.flush()


def _platform(monkeypatch):
    monkeypatch.setenv("PLATFORM_AI_PROVIDER", "gemini")
    monkeypatch.setenv("PLATFORM_AI_KEY", PLATFORM_KEY)


def _providers(monkeypatch) -> list[httpx.Request]:
    """Gemini and OpenAI as the real adapters over a mock transport: every request they make."""
    from libs.llm import providers
    from libs.llm.rest import GeminiClient, OpenAIClient

    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "generativelanguage" in str(request.url):
            return httpx.Response(200, json={"candidates": [{
                "content": {"parts": [{"text": WRITTEN}]}, "finishReason": "STOP"}]})
        return httpx.Response(200, json={"choices": [{
            "message": {"content": WRITTEN}, "finish_reason": "stop"}]})

    http = httpx.Client(transport=httpx.MockTransport(answer))
    monkeypatch.setattr(providers, "GeminiClient", lambda key: GeminiClient(key, http=http))
    monkeypatch.setattr(providers, "OpenAIClient", lambda key: OpenAIClient(key, http=http))
    return seen


# ── the free one ──────────────────────────────────────────────────────────────────────────────

def test_an_instant_cv_is_asked_for_built_and_downloaded(api):
    asked = _ask(api)
    assert asked.status_code == 202
    assert asked.json()["state"] == "queued"

    assert _work(api)

    status = _status(api, asked.json()["id"])
    assert status["state"] == "ready", status
    download = api.http.get(status["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert download.content.startswith(b"%PDF")
    assert 'filename="Alice_Example_CV.pdf"' in download.headers["content-disposition"]
    assert "Built the billing service in Python and Postgres" in api.rendered[0]


def test_the_document_built_is_the_kind_asked_for(api):
    """A cover letter asked for must not come back as a CV."""
    build_id = _ask(api, kind="cover").json()["id"]
    _work(api)

    download = api.http.get(f"/documents/build/{build_id}/file")
    assert 'filename="Alice_Example_Cover.pdf"' in download.headers["content-disposition"]
    assert "Dear Hiring Manager" in api.rendered[0]


def test_asking_twice_while_it_waits_is_the_same_build(api):
    assert _ask(api).json()["id"] == _ask(api).json()["id"]


def test_a_build_is_its_owners_alone(api):
    from libs.auth.access import issue_access_token
    from libs.db.models import User

    build_id = _ask(api).json()["id"]
    _work(api)
    stranger = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
    api.session.add(stranger)
    api.session.flush()
    api.http.cookies.set("access_token", issue_access_token(stranger.id, uuid.uuid4()), path="/")

    assert api.http.get(f"/documents/build/{build_id}").status_code == 404
    assert api.http.get(f"/documents/build/{build_id}/file").status_code == 404


def test_a_refusal_says_what_to_do_and_is_not_a_dead_letter(api):
    """A dead letter pages the operator; a person with no CV uploaded is not an incident."""
    from sqlalchemy import delete

    from libs.db.models import QueueJob, UserDocument

    api.session.execute(delete(UserDocument).where(UserDocument.user_id == api.user.id))
    build_id = _ask(api).json()["id"]
    _work(api)

    status = _status(api, build_id)
    assert status["state"] == "failed"
    assert "upload your CV" in status["detail"]
    assert api.session.get(QueueJob, build_id).state == "done"


def test_a_build_from_a_telegram_tap_is_refused_rather_than_lost(api):
    """Taps queued before documents moved onto the site carry no mode, and nowhere to send to."""
    from libs.db.models import QueueJob
    from libs.queue import enqueue

    build_id = enqueue(api.session, "docgen", api.user.id, payload={"job_id": api.job.id})
    _work(api)

    assert api.session.get(QueueJob, build_id).payload["refused"].startswith("documents are built")


def test_a_built_document_is_deleted_once_it_has_been_kept_long_enough(api):
    """D9: kept for `document_keep_hours`, downloaded or not, and the page says so afterwards."""
    from libs.db.models import BuiltDocument
    from services.docgen.__main__ import purge

    old = _ask(api).json()["id"]
    _work(api)
    fresh = _ask(api, kind="cover").json()["id"]
    _work(api)
    api.session.get(BuiltDocument, old).created_at = datetime.now(UTC) - timedelta(hours=2)
    api.session.flush()

    purge(api.session)

    assert _status(api, old)["state"] == "expired"
    assert api.http.get(f"/documents/build/{old}/file").status_code == 404
    assert _status(api, fresh)["state"] == "ready"


def test_the_nightly_dump_leaves_built_documents_out(api):
    """Both halves of one rule: the table says its rows are ephemeral, and the dump skips such rows.
    A CV kept here for an hour must not be kept for thirty days in a backup."""
    from sqlalchemy import text

    spec = importlib.util.spec_from_file_location(
        "backup", pathlib.Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "backup.py")
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)

    persistence = api.session.scalar(
        text("SELECT relpersistence FROM pg_class WHERE relname = 'built_documents'"))
    assert persistence == "u", "built_documents is not UNLOGGED, so the dump keeps every CV in it"
    assert "--no-unlogged-table-data" in backup._pg_dump_args("postgresql+psycopg://u@h/db")


# ── the AI one ────────────────────────────────────────────────────────────────────────────────

def test_an_ai_document_asks_for_consent_first(api, monkeypatch):
    _platform(monkeypatch)
    _personalise(api)

    refused = _ask(api, mode="ai")

    assert refused.status_code == 403
    assert "agree" in refused.json()["detail"]


def test_withdrawn_consent_stops_a_queued_ai_document_before_any_text_leaves(api, monkeypatch):
    """The guarantee sits where text leaves the server, whatever was decided when it was queued."""
    _platform(monkeypatch)
    _personalise(api)
    _consent(api)
    sent = _providers(monkeypatch)
    build_id = _ask(api, mode="ai").json()["id"]

    api.http.delete("/account/consent")
    _work(api)

    status = _status(api, build_id)
    assert status["state"] == "failed" and "agree" in status["detail"]
    assert sent == [], "the profile was sent to a provider without consent"


def test_an_ai_document_on_the_platform_key_writes_with_the_operators_model(api, monkeypatch):
    """Allowance, platform key, the model chosen in the panel, the provider asked, a PDF back."""
    from libs import settings
    from libs.llm import ai_requests_today

    _platform(monkeypatch)
    _personalise(api)
    _consent(api)
    settings.put(api.session, "document_models", {"gemini": "gemini-chosen-in-the-panel"})
    sent = _providers(monkeypatch)

    build_id = _ask(api, mode="ai").json()["id"]
    _work(api)

    assert _status(api, build_id)["state"] == "ready"
    assert "models/gemini-chosen-in-the-panel:generateContent" in str(sent[0].url)
    assert sent[0].headers["x-goog-api-key"] == PLATFORM_KEY
    assert PLATFORM_KEY not in str(sent[0].url), "a key in the URL ends up in every error message"
    assert ai_requests_today(api.session, api.user.id) == 1


def test_their_own_key_pays_and_is_never_counted_against_the_allowance(api, monkeypatch):
    from libs.llm import ai_requests_today

    _platform(monkeypatch)
    _personalise(api)
    _consent(api)
    api.store.put(api.user.id, "openai_api", OWN_KEY, validated=True)
    sent = _providers(monkeypatch)

    build_id = _ask(api, mode="ai").json()["id"]
    _work(api)

    assert _status(api, build_id)["state"] == "ready"
    assert sent[0].headers["authorization"] == f"Bearer {OWN_KEY}"
    assert ai_requests_today(api.session, api.user.id) == 0


def test_without_any_key_ai_says_so_and_the_instant_version_still_works(api):
    """No key of their own and no platform key configured: AI is unavailable, nothing else is."""
    _personalise(api)
    _consent(api)

    refused = _ask(api, mode="ai")

    assert refused.status_code == 409
    assert "own Claude, Gemini or OpenAI key" in refused.json()["detail"]
    assert _ask(api, mode="instant").status_code == 202


def test_the_allowance_is_one_count_that_the_chat_spends_too(api, monkeypatch):
    """A reply in the support chat and a document come out of the same daily allowance."""
    from libs.limits import set_limit
    from libs.llm import ai_requests_today, record_ai_request

    _platform(monkeypatch)
    _personalise(api)
    _consent(api)
    set_limit(api.session, api.user.id, "ai_platform_requests_per_day", 2)
    record_ai_request(api.session, api.user.id)          # what the chat does for one reply

    assert _ask(api, kind="cv", mode="ai").status_code == 202
    assert _ask(api, kind="cv", mode="ai").status_code == 202, "asking twice is one request"
    over = _ask(api, kind="cover", mode="ai")

    assert over.status_code == 409
    assert ai_requests_today(api.session, api.user.id) == 2


def test_the_daily_ceiling_counts_every_document_where_they_are_asked_for(api):
    """`cv_builds_per_day` is the one ceiling on documents, instant or AI, at the one place they
    are asked for — a generous one, since an instant document is a render."""
    from libs.limits import BY_KEY, set_limit

    _personalise(api)
    _consent(api)
    api.store.put(api.user.id, "gemini_api", "AIzaOwn-" + "g" * 30, validated=True)
    set_limit(api.session, api.user.id, "cv_builds_per_day", 2)

    assert _ask(api, kind="cv", mode="instant").status_code == 202
    assert _ask(api, kind="cv", mode="ai").status_code == 202
    refused = _ask(api, kind="cover", mode="instant")
    assert refused.status_code == 429
    assert "2 documents" in refused.json()["detail"]
    assert BY_KEY["cv_builds_per_day"].default == 50


def test_an_account_without_a_writing_profile_is_offered_the_instant_version(api, monkeypatch):
    """AI documents for everyone else wait for the shared prompts (ROADMAP Phase 8, step 7)."""
    _platform(monkeypatch)
    _consent(api)

    refused = _ask(api, mode="ai")

    assert refused.status_code == 409
    assert "instant version" in refused.json()["detail"]
