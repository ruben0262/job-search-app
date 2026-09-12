"""Document extraction and upload."""
from __future__ import annotations

import io
import os
import uuid

import pytest

pytest.importorskip("docx")
pytest.importorskip("pypdf")

import docx
from pypdf import PdfWriter

from libs.documents import MAX_TEXT_CHARS, UnsupportedDocument, extract_text


def _docx_bytes(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for row_index, row in enumerate(table):
            for cell_index, value in enumerate(row):
                grid.cell(row_index, cell_index).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _blank_pdf_bytes(pages: int = 1) -> bytes:
    """A PDF with no text layer — what a scanned CV looks like to a parser."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ── extraction ────────────────────────────────────────────────────────────────────────────────

def test_plain_text_keeps_its_line_structure():
    """A CV is a list because of its line breaks."""
    extracted = extract_text("cv.md", b"# Alice\n\nBackend Engineer\n- Go\n- Postgres\n")
    assert extracted.text.splitlines() == ["# Alice", "", "Backend Engineer", "- Go", "- Postgres"]


def test_runs_of_blank_lines_collapse_to_one():
    """PDF extractors emit them by the dozen, and they are pure cost in a conversation."""
    assert extract_text("a.txt", b"one\n\n\n\n\ntwo").text == "one\n\ntwo"


def test_a_docx_table_is_read():
    """CVs routinely lay out dates, employers and skills matrices in a table."""
    data = _docx_bytes(["Alice Example"], table=[["2019-2022", "Acme"], ["2022-", "Globex"]])
    text = extract_text("cv.docx", data).text
    assert "Alice Example" in text
    assert "2019-2022 | Acme" in text
    assert "2022- | Globex" in text


def test_a_scan_with_no_text_layer_says_so():
    """The single most likely bad upload."""
    with pytest.raises(UnsupportedDocument, match="no readable text"):
        extract_text("scan.pdf", _blank_pdf_bytes())


def test_an_unreadable_file_does_not_leak_the_parser_error():
    """A parser's exception text is meaningless to the person at the form and occasionally quotes file."""
    with pytest.raises(UnsupportedDocument) as exc:
        extract_text("cv.pdf", b"this is not a pdf at all")
    assert "could not be read" in str(exc.value)
    assert "pypdf" not in str(exc.value).lower()


@pytest.mark.parametrize("filename", ["cv.exe", "cv.pages", "archive.zip", "noextension"])
def test_an_unsupported_format_names_what_is_supported(filename):
    with pytest.raises(UnsupportedDocument, match=r"\.docx"):
        extract_text(filename, b"anything")


def test_an_oversized_file_is_refused_before_it_is_parsed():
    """The ceiling exists so a decompression bomb is a rejection rather than an outage."""
    with pytest.raises(UnsupportedDocument, match="the limit is"):
        extract_text("big.pdf", b"x" * (11 * 1024 * 1024))


def test_very_long_text_is_truncated_and_says_so():
    """Silently keeping the first N characters would give a user a profile built from half their."""
    extracted = extract_text("history.txt", ("word " * (MAX_TEXT_CHARS // 4)).encode())
    assert extracted.truncated is True
    assert extracted.chars == MAX_TEXT_CHARS


def test_a_document_the_size_of_a_real_history_is_kept_whole():
    """The number that decides how much of a person's own writing the platform holds.

    Raised to 300,000 on 2026-08-13. The account that prompted it had uploaded 145,493 characters,
    which every ceiling on the platform then stored and no ceiling let the setup agent read.
    """
    # Longer than the 145,493-character file that prompted the change, and longer than the 200,000
    # this used to stop at.
    body = ("word " * 45_000).strip()
    assert len(body) > 145_493
    extracted = extract_text("context.txt", body.encode())
    assert extracted.truncated is False
    assert extracted.chars == len(body)


def test_the_same_content_in_two_formats_hashes_the_same():
    """The hash is over the extracted text, not the file."""
    first = extract_text("cv.txt", b"Alice Example\nBackend Engineer")
    second = extract_text("cv.md", b"Alice Example\nBackend Engineer\n")
    assert first.sha256 == second.sha256


def test_a_legacy_encoding_is_read_rather_than_rejected():
    """A CV exported from an old word processor is often cp1252."""
    assert "Andr" in extract_text("cv.txt", "András Lekli".encode("latin-1")).text


# ── the routes ────────────────────────────────────────────────────────────────────────────────

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def api():
    pytest.importorskip("fastapi")
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

    users = []
    for _ in range(2):
        user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active")
        session.add(user)
        users.append(user)
    session.flush()

    app.dependency_overrides[deps.get_db] = lambda: session
    try:
        with TestClient(app, base_url="https://testserver") as http:
            http.cookies.set("access_token",
                             issue_access_token(users[0].id, uuid.uuid4()), path="/")
            yield http, session, users
    finally:
        app.dependency_overrides.clear()
        session.close()
        trans.rollback()
        conn.close()
        os.environ.pop("DISABLE_RATE_LIMIT", None)


def _upload(http, name="cv.txt", body=b"Alice Example\nBackend Engineer at Acme", kind="cv"):
    return http.post("/documents", data={"kind": kind},
                     files={"file": (name, body, "text/plain")})


@pytestmark_db
def test_an_upload_stores_the_text_and_not_the_file(api):
    """Nothing downstream reads a PDF, and keeping strangers' records on disk would be a liability with."""
    from libs.db.models import UserDocument

    http, session, users = api
    response = _upload(http)
    session.flush()

    assert response.status_code == 201
    (stored,) = session.query(UserDocument).filter_by(user_id=users[0].id).all()
    assert "Backend Engineer at Acme" in stored.text
    assert not hasattr(stored, "content"), "no column may hold the original bytes"


@pytestmark_db
def test_re_uploading_the_same_file_replaces_rather_than_duplicates(api):
    """Users press the button twice, and two copies of one CV is one CV of signal at twice the processing."""
    from sqlalchemy import func, select

    from libs.db.models import UserDocument

    http, session, users = api
    _upload(http)
    session.flush()
    second = _upload(http)
    session.flush()

    assert second.json()["status"] == "replaced"
    assert session.scalar(select(func.count()).select_from(UserDocument)
                          .where(UserDocument.user_id == users[0].id)) == 1


@pytestmark_db
def test_a_file_longer_than_we_read_is_kept_and_the_uploader_is_told(api):
    """The person can shorten it; the model cannot.

    The truncation was reported into the setup agent's tool result, so the only reader was a model
    that could do nothing about it, while the person who could went on believing their whole file
    had been read. It is not a refusal — the document is stored and usable — so it comes back as a
    note beside a 201, not an error.
    """
    http, _, _ = api
    response = _upload(http, name="history.txt", body=("word " * (MAX_TEXT_CHARS // 4)).encode(),
                       kind="history")

    assert response.status_code == 201
    body = response.json()
    assert body["truncated"] is True
    assert body["chars"] == MAX_TEXT_CHARS
    assert body["note"], "a truncated upload must come back with something to show the user"
    assert f"{MAX_TEXT_CHARS:,}" in body["note"], "the note has to say how much was kept"


@pytestmark_db
def test_an_ordinary_upload_carries_no_warning(api):
    """A note on every upload is a note nobody reads."""
    http, _, _ = api
    body = _upload(http).json()
    assert body["truncated"] is False
    assert body["note"] is None


@pytestmark_db
def test_a_bad_file_is_refused_with_something_the_user_can_act_on(api):
    http, _, _ = api
    response = http.post("/documents", data={"kind": "cv"},
                         files={"file": ("cv.exe", b"MZ", "application/octet-stream")})
    assert response.status_code == 422
    assert ".docx" in response.json()["detail"]


@pytestmark_db
def test_the_list_shows_a_preview_not_the_whole_history(api):
    """Returning the full text would put a person's complete employment history into a response the."""
    http, session, _ = api
    _upload(http, body=("Employment history. " * 500).encode())
    session.flush()

    body = http.get("/documents").json()
    assert body["documents"][0]["chars"] > 200
    assert len(body["documents"][0]["preview"]) <= 200


@pytestmark_db
def test_one_user_cannot_delete_anothers_document(api):
    """T1 at the document layer. An id from another account must find nothing, not delete it."""
    from libs.db.models import UserDocument

    http, session, users = api
    other = UserDocument(user_id=users[1].id, kind="cv", filename="theirs.txt",
                         text="not yours", text_sha256="a" * 64, chars=9)
    session.add(other)
    session.flush()

    assert http.delete(f"/documents/{other.id}").status_code == 404
    assert session.get(UserDocument, other.id) is not None


@pytestmark_db
def test_uploading_requires_signing_in(api):
    http, _, _ = api
    http.cookies.clear()
    assert _upload(http).status_code == 401
