"""A CV or cover letter for a job: asked for on the site, built by the docgen worker, downloaded,
deleted. The real app and the real worker step share one test database; only Chrome and the AI
providers are stand-ins, and the providers are reached through their real adapters."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
import httpx

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
def api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import CorpusJob, User, UserDocument
    from libs.secrets import KeyRing, SecretStore, generate_key
    from services.api.app import app
    from services.docgen import pdf

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    kek = f"k1:{generate_key()}"
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("SECRETS_KEK", kek)
    monkeypatch.delenv("PLATFORM_AI_PROVIDER", raising=False)
    monkeypatch.delenv("PLATFORM_AI_KEY", raising=False)

    user = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                           full_name="Alice Example"))
    insert(db, UserDocument(user_id=user.id, kind="cv", filename="cv.txt", text=CV_TEXT,
                            text_sha256=hashlib.sha256(CV_TEXT.encode()).hexdigest(),
                            chars=len(CV_TEXT)))
    job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", source="test",
                               title="Backend Engineer", company="Northwind",
                               description="Backend engineer. Python, Postgres, on-call."))

    # Chrome, as a function: what it was handed, and a PDF back.
    rendered: list[str] = []
    monkeypatch.setattr("services.docgen.run.pdf.render",
                        lambda html: rendered.append(html) or b"%PDF-1.4 test")
    monkeypatch.setattr("services.docgen.run.pdf.inspect_pdf",
                        lambda document, kind="cv": pdf.PdfReport(pages=1, chars=900, issues=[]))
    # The posting's freshness check fetches the posting; nothing here may touch the network.
    monkeypatch.setattr("services.docgen.run.refresh_before_build", lambda *a, **k: {})

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")
        yield SimpleNamespace(http=http, db=db, user=user, job=job, rendered=rendered,
                              store=SecretStore(db, KeyRing.from_env({"SECRETS_KEK": kek})))


def _ask(api, kind="cv", mode="instant"):
    return api.http.post("/documents/build", json={"job_id": api.job.id, "kind": kind, "mode": mode})


def _work(api) -> bool:
    """One step of the real docgen worker: claim, handle, complete."""
    from libs.queue.worker import WorkerStats, run_once
    from services.docgen.__main__ import handle

    return run_once(api.db, "docgen", "test-docgen", handle, WorkerStats())


def _status(api, build_id: int) -> dict:
    return api.http.get(f"/documents/build/{build_id}").json()


def _consent(api):
    version = api.http.get("/account/consent").json()["current_version"]
    assert api.http.post("/account/consent", json={"version": version}).status_code == 200


def _personalise(api):
    """What the setup chat left a legacy account with: the files the tailoring prompts need."""
    from libs.profiles.repository import ProfileFiles

    files = ProfileFiles(api.db, api.user.id)
    files.write("context.md", "# Alice Example\nBackend engineer in Berlin. Python, Postgres.",
                kind="context")
    files.write("base_cv.html", WRITTEN, kind="template")
    files.write("tailor_cv.md", "Tailor Alice Example's CV.\n<JOB_CONTEXT>\n<BASE_TEMPLATE>",
                kind="prompt")
    files.write("tailor_cover.md", "Write Alice Example a letter.\n<HEAD_BLOCK>\n<JOB_CONTEXT>",
                kind="prompt")


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


def test_making_a_document_is_not_applying(api):
    """Instant documents are free previews; only "I applied" on the jobs page records an application."""
    from libs.db import find_one
    from libs.db.models import UserJobState

    _ask(api)
    assert _work(api)
    state = find_one(api.db, UserJobState, {"user_id": api.user.id, "job_id": api.job.id})
    assert state is None or state.applied_at is None


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
    from libs.db import insert
    from libs.db.models import User

    build_id = _ask(api).json()["id"]
    _work(api)
    assert api.http.get(f"/documents/build/{build_id}/file").status_code == 200, "the premise"
    stranger = insert(api.db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    api.http.cookies.set("access_token", issue_access_token(stranger.id, uuid.uuid4()), path="/")

    assert api.http.get(f"/documents/build/{build_id}").status_code == 404
    assert api.http.get(f"/documents/build/{build_id}/file").status_code == 404


def test_a_refusal_says_what_to_do_and_is_not_a_dead_letter(api):
    """A dead letter pages the operator; a person with no CV uploaded is not an incident."""
    from libs.db import get
    from libs.db.models import QueueJob, UserDocument

    api.db[UserDocument.COLLECTION].delete_many({"user_id": api.user.id})
    build_id = _ask(api).json()["id"]
    _work(api)

    status = _status(api, build_id)
    assert status["state"] == "failed"
    assert "upload your CV" in status["detail"]
    assert get(api.db, QueueJob, build_id).state == "done"


def test_a_build_from_a_telegram_tap_is_refused_rather_than_lost(api):
    """Taps queued before documents moved onto the site carry no mode, and nowhere to send to."""
    from libs.db import get
    from libs.db.models import QueueJob
    from libs.queue import enqueue

    build_id = enqueue(api.db, "docgen", api.user.id, payload={"job_id": api.job.id})
    _work(api)

    assert get(api.db, QueueJob, build_id).payload["refused"].startswith("documents are built")


def _big_pdfs(api, monkeypatch, kilobytes: int = 700) -> None:
    """Chrome handing back PDFs this size, and an account allowed to keep 1 MB of them."""
    from libs.db.models import User

    monkeypatch.setattr("services.docgen.run.pdf.render", lambda html: b"%PDF-1.4 " + b"x" * kilobytes * 1024)
    api.db[User.COLLECTION].update_one({"_id": api.user.id}, {"$set": {"limits": {"document_storage_mb": 1}}})


def _make(api, kind="cv") -> dict:
    build_id = _ask(api, kind=kind).json()["id"]
    _work(api)
    return _status(api, build_id)


def test_a_made_document_is_kept_to_download_again_and_shows_on_the_jobs_it_was_for(api):
    """After a refresh, from the Applied list, or from My documents: the same PDF, not a new one."""
    made = _make(api)
    assert (made["state"], made["saved"]) == ("ready", True)

    api.http.put(f"/jobs/{api.job.id}/applied")
    (applied,) = api.http.get("/applied").json()
    (document,) = applied["documents"]
    assert (document["kind"], document["download_url"]) == ("cv", made["download_url"])
    assert api.http.get(document["download_url"]).content.startswith(b"%PDF")

    kept = api.http.get("/saved").json()
    assert [(each["kind"], each["job_title"]) for each in kept["documents"]] == [("cv", "Backend Engineer")]
    assert kept["used_bytes"] == kept["documents"][0]["size"] > 0
    assert kept["allowance_bytes"] == 5 * 1024 * 1024, "the catalogue default"


def test_a_cover_letter_built_keeps_its_posting_and_every_document_its_ats_verdict(api):
    """Retention keeps a posting somebody wrote a cover letter for (`ACTED_ON`), but nothing said they
    had until 2026-11-03. And each document keeps whether it passed the ATS check, so outcomes can say
    whether passing it helps people hear back."""
    from libs.db import find_one
    from libs.db.models import BuiltDocument, UserJobState

    _make(api)
    before = find_one(api.db, UserJobState, {"user_id": api.user.id, "job_id": api.job.id})
    assert before is None or before.cover_built is False, "a CV alone is not a cover letter"

    _make(api, kind="cover")

    state = find_one(api.db, UserJobState, {"user_id": api.user.id, "job_id": api.job.id})
    assert state.cover_built is True
    documents = api.db[BuiltDocument.COLLECTION].find({"user_id": api.user.id})
    assert {document["ats_ok"] for document in documents} == {True}


def test_a_new_one_for_the_same_job_and_kind_replaces_the_old(api):
    first, second = _make(api), _make(api)
    assert first["id"] != second["id"]
    assert [each["id"] for each in api.http.get("/saved").json()["documents"]] == [second["id"]]
    assert api.http.get(first["download_url"]).status_code == 404


def test_a_full_storage_keeps_nothing_new_but_it_still_downloads_and_the_person_is_told(api, monkeypatch):
    """Their own allowance (`document_storage_mb`): what does not fit waits only for its download, as
    every document used to (D9), and deleting one makes room again."""
    from libs.db import now
    from libs.db.models import BuiltDocument
    from services.docgen.__main__ import purge

    _big_pdfs(api, monkeypatch)
    cv = _make(api)
    cover = _make(api, kind="cover")

    assert cv["saved"] is True and cover["saved"] is False
    assert api.http.get(cover["download_url"]).status_code == 200, "made now, so it can be had now"
    told = api.http.get("/notifications").json()["notifications"]
    assert any(each["title"] == "Your document storage is full" for each in told)
    assert [each["kind"] for each in api.http.get("/saved").json()["documents"]] == ["cv"]

    api.db[BuiltDocument.COLLECTION].update_many({}, {"$set": {"created_at": now() - timedelta(hours=2)}})
    purge(api.db)
    assert _status(api, cover["id"])["state"] == "expired", "not kept, so gone after its hour"
    assert api.http.get(cv["download_url"]).status_code == 200, "kept, so still there"

    api.http.delete(f"/saved/{cv['id']}")
    assert _make(api, kind="cover")["saved"] is True, "deleting one made room"


def test_clearing_all_empties_the_storage(api, monkeypatch):
    _big_pdfs(api, monkeypatch, kilobytes=300)
    made = [_make(api)["id"], _make(api, kind="cover")["id"]]

    assert api.http.delete("/saved").status_code == 422, "naming none deletes nothing"
    cleared = api.http.delete("/saved", params={"ids": made}).json()

    assert (cleared["documents"], cleared["used_bytes"]) == ([], 0)


def test_the_ones_picked_out_are_deleted_and_the_rest_kept(api):
    """My documents deletes what it shows: the older ones, or the ones a search found."""
    cv, cover = _make(api), _make(api, kind="cover")

    left = api.http.delete("/saved", params={"ids": [cv["id"]]}).json()

    assert [each["id"] for each in left["documents"]] == [cover["id"]]
    assert api.http.get(cv["download_url"]).status_code == 404


def test_a_kept_document_is_its_owners_alone(api):
    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import User

    made = _make(api)
    stranger = insert(api.db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
    api.http.cookies.set("access_token", issue_access_token(stranger.id, uuid.uuid4()), path="/")

    assert api.http.get(made["download_url"]).status_code == 404
    assert api.http.delete(f"/saved/{made['id']}").status_code == 404
    api.http.delete("/saved", params={"ids": [made["id"]]})
    assert api.http.get("/saved").json()["documents"] == []
    api.http.cookies.set("access_token", issue_access_token(api.user.id, uuid.uuid4()), path="/")
    assert api.http.get(made["download_url"]).status_code == 200, "a stranger's pick deletes nothing"


def _backup():
    spec = importlib.util.spec_from_file_location(
        "backup", pathlib.Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "backup.py")
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    return backup


def test_the_nightly_backup_does_not_keep_built_documents():
    """Both halves of one rule: the collection says its PDFs are made again in seconds, and the backup
    skips such collections. A CV a person deleted must not live on for thirty days in a backup."""
    from libs.db.models import BuiltDocument, User

    kept = _backup().kept()
    assert BuiltDocument.BACKED_UP is False
    assert BuiltDocument.COLLECTION not in kept
    assert User.COLLECTION in kept, "the list is the backup's, not an empty one"


def test_a_written_backup_holds_no_built_document(api, tmp_path):
    """The same rule, on the file the backup actually writes, with a built CV in the database."""
    from libs.db.models import BuiltDocument, User

    backup = _backup()
    build_id = _ask(api).json()["id"]
    _work(api)
    assert api.db[BuiltDocument.COLLECTION].count_documents({"_id": build_id}) == 1, "the premise"

    target = tmp_path / "backup.jsonl.gz"
    counts = backup.write(api.db, target)

    assert counts.get(User.COLLECTION), "the backup holds the accounts, so it is a real one"
    assert BuiltDocument.COLLECTION not in counts
    assert not [doc for name, doc in backup.read(target) if name == BuiltDocument.COLLECTION]
    assert backup.verify(target)


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
    settings.put(api.db, "document_models", {"gemini": "gemini-chosen-in-the-panel"})
    sent = _providers(monkeypatch)

    build_id = _ask(api, mode="ai").json()["id"]
    _work(api)

    assert _status(api, build_id)["state"] == "ready"
    assert "models/gemini-chosen-in-the-panel:generateContent" in str(sent[0].url)
    assert sent[0].headers["x-goog-api-key"] == PLATFORM_KEY
    assert PLATFORM_KEY not in str(sent[0].url), "a key in the URL ends up in every error message"
    assert ai_requests_today(api.db, api.user.id) == 1


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
    assert ai_requests_today(api.db, api.user.id) == 0


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
    set_limit(api.db, api.user.id, "ai_platform_requests_per_day", 2)
    record_ai_request(api.db, api.user.id)          # what the chat does for one reply

    assert _ask(api, kind="cv", mode="ai").status_code == 202
    assert _ask(api, kind="cv", mode="ai").status_code == 202, "asking twice is one request"
    over = _ask(api, kind="cover", mode="ai")

    assert over.status_code == 409
    assert ai_requests_today(api.db, api.user.id) == 2


def test_the_daily_ceiling_counts_every_document_where_they_are_asked_for(api):
    """`cv_builds_per_day` is the one ceiling on documents, instant or AI, at the one place they
    are asked for — a generous one, since an instant document is a render."""
    from libs.limits import BY_KEY, set_limit

    _personalise(api)
    _consent(api)
    api.store.put(api.user.id, "gemini_api", "AIzaOwn-" + "g" * 30, validated=True)
    set_limit(api.db, api.user.id, "cv_builds_per_day", 2)

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
