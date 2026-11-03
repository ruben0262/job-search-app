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
from tests.conftest import needs_db


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

@pytest.fixture()
def api(db, monkeypatch):
    pytest.importorskip("fastapi")
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from libs.auth.access import issue_access_token
    from libs.db import insert
    from libs.db.models import User
    from services.api.app import app

    users = [insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active"))
             for _ in range(2)]

    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(users[0].id, uuid.uuid4()), path="/")
        yield http, db, users


def _upload(http, name="cv.txt", body=b"Alice Example\nBackend Engineer at Acme", kind="cv"):
    return http.post("/documents", data={"kind": kind},
                     files={"file": (name, body, "text/plain")})


@needs_db
def test_an_upload_stores_the_text_and_not_the_file(api):
    """Nothing downstream reads a PDF, and keeping strangers' records on disk would be a liability with."""
    from libs.db.models import UserDocument

    http, db, users = api
    response = _upload(http)

    assert response.status_code == 201
    (stored,) = db[UserDocument.COLLECTION].find({"user_id": users[0].id})
    assert "Backend Engineer at Acme" in stored["text"]
    assert not [key for key, value in stored.items() if isinstance(value, bytes)], \
        "no field may hold the original bytes"
    assert "content" not in UserDocument.__dataclass_fields__


@needs_db
def test_re_uploading_the_same_file_replaces_rather_than_duplicates(api):
    """Users press the button twice, and two copies of one CV is one CV of signal at twice the processing."""
    from libs.db.models import UserDocument

    http, db, users = api
    _upload(http)
    second = _upload(http)

    assert second.json()["status"] == "replaced"
    assert db[UserDocument.COLLECTION].count_documents({"user_id": users[0].id}) == 1


@needs_db
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


@needs_db
def test_an_ordinary_upload_carries_no_warning(api):
    """A note on every upload is a note nobody reads."""
    http, _, _ = api
    body = _upload(http).json()
    assert body["truncated"] is False
    assert body["note"] is None


@needs_db
def test_a_bad_file_is_refused_with_something_the_user_can_act_on(api):
    http, _, _ = api
    response = http.post("/documents", data={"kind": "cv"},
                         files={"file": ("cv.exe", b"MZ", "application/octet-stream")})
    assert response.status_code == 422
    assert ".docx" in response.json()["detail"]


@needs_db
def test_the_list_shows_a_preview_not_the_whole_history(api):
    """Returning the full text would put a person's complete employment history into a response the."""
    http, _db, _ = api
    _upload(http, body=("Employment history. " * 500).encode())

    body = http.get("/documents").json()
    assert body["documents"][0]["chars"] > 200
    assert len(body["documents"][0]["preview"]) <= 200


@needs_db
def test_one_user_cannot_delete_anothers_document(api):
    """T1 at the document layer. An id from another account must find nothing, not delete it."""
    from libs.db import get, insert
    from libs.db.models import UserDocument

    http, db, users = api
    other = insert(db, UserDocument(user_id=users[1].id, kind="cv", filename="theirs.txt",
                                    text="not yours", text_sha256="a" * 64, chars=9))

    assert http.delete(f"/documents/{other.id}").status_code == 404
    assert get(db, UserDocument, other.id) is not None


@needs_db
def test_uploading_requires_signing_in(api):
    http, _, _ = api
    http.cookies.clear()
    assert _upload(http).status_code == 401


@needs_db
def test_a_cv_switched_off_is_kept_but_nothing_is_made_from_it_until_it_is_switched_on(api):
    """Two CVs for two kinds of role are both used; one switched off stays listed and leaves the CVs
    made for jobs, and switched on again it is back in them."""
    from types import SimpleNamespace

    from libs.documents.instant import render
    from libs.suggestions import documents_of

    http, db, users = api
    _upload(http, name="mechanical.txt",
            body=b"Alice Example\nEXPERIENCE\nMechanical Engineer, Acme\t2019 - 2024\n"
                 b"- Ran thermal tests on pump housings")
    design = _upload(http, name="design.txt",
                     body=b"Alice Example\nEXPERIENCE\nDesign Engineer, Globex\t2015 - 2019\n"
                          b"- Drew CAD models of gearboxes in SolidWorks").json()["id"]
    job = SimpleNamespace(title="Mechanical Design Engineer", company="Initech", location="", url="",
                          description="CAD, SolidWorks, thermal testing")

    def made() -> str:
        return render("cv", documents=documents_of(db, users[0].id), identity={}, job=job)

    assert "pump housings" in made() and "gearboxes" in made(), "both CVs are used"

    switched = http.patch(f"/documents/{design}", json={"in_use": False})
    assert switched.status_code == 200 and switched.json()["in_use"] is False
    listed = {row["filename"]: row["in_use"] for row in http.get("/documents").json()["documents"]}
    assert listed == {"mechanical.txt": True, "design.txt": False}, "kept and listed, said to be off"
    assert "pump housings" in made() and "gearboxes" not in made()

    http.patch(f"/documents/{design}", json={"in_use": True})
    assert "gearboxes" in made()


@needs_db
def test_uploading_a_switched_off_file_again_switches_it_back_on(api):
    http, _, _ = api
    stored = _upload(http).json()["id"]
    http.patch(f"/documents/{stored}", json={"in_use": False})
    _upload(http)
    assert http.get("/documents").json()["documents"][0]["in_use"] is True


@needs_db
def test_one_user_cannot_switch_anothers_document(api):
    from libs.db import get, insert
    from libs.db.models import UserDocument

    http, db, users = api
    other = insert(db, UserDocument(user_id=users[1].id, kind="cv", filename="theirs.txt",
                                    text="not yours", text_sha256="b" * 64, chars=9))

    assert http.patch(f"/documents/{other.id}", json={"in_use": False}).status_code == 404
    assert get(db, UserDocument, other.id).in_use is True
