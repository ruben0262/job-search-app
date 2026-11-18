"""The legal pages, as behaviour: each one whole and filled in, the operator's details where the law wants
them, the Cookie Policy naming everything the site keeps on a device, and the retention it states kept by the
database."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("terms", "privacy", "cookies", "data")


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
    from tests.conftest import agreed

    admin = insert(db, User(email=f"{uuid.uuid4()}@example.com", password_hash="x", status="active",
                            is_admin=True, **agreed()))
    with TestClient(app, base_url="https://testserver") as http:
        http.cookies.set("access_token", issue_access_token(admin.id, uuid.uuid4()), path="/")
        yield http, db


def test_every_page_is_whole_with_nothing_left_unfilled(api):
    """A gap left in a page is a sentence with a hole in it, said to everybody; a page without its short
    version leaves the agree screen, which shows the Terms' own, with nothing to show."""
    from libs.legal import LEGAL_VERSION
    from libs.legal.documents import DOCUMENTS

    http, _ = api
    for name in PAGES:
        body = http.get(f"/legal/{name}").json()
        text = body["markdown"]
        assert (body["title"], body["version"]) == (DOCUMENTS[name], LEGAL_VERSION)
        assert text.startswith(f"# {DOCUMENTS[name]}\n"), name
        assert "> **The short version**" in text, name
        assert "{" not in text and "}" not in text, f"{name}: a gap was left unfilled"


def test_the_operators_details_set_in_admin_are_on_the_pages(api):
    """Who runs the site, as the law wants it said before anybody signs up or pays: typed in Admin, never kept
    in the code, and said where it belongs on the pages, or said to be missing."""
    from libs.legal.documents import NOT_SET

    http, _ = api
    assert NOT_SET in http.get("/legal/terms").json()["markdown"], "an empty detail is said to be missing"

    details = {"name": "Example Kft.", "address": "1111 Budapest, Példa utca 1.",
               "registration": "01-09-123456", "tax_number": "12345678-2-42", "phone": "+36 1 234 5678",
               "hosting": "Example Hosting Zrt., 1111 Budapest, hosting@example.com"}
    assert http.put("/admin/settings/operator", json={"value": details}).status_code == 200

    terms = http.get("/legal/terms").json()["markdown"]
    for field, value in details.items():
        assert value in terms, field
    assert NOT_SET not in terms
    assert details["name"] in http.get("/legal/privacy").json()["markdown"], "the controller is named"

    for wrong in ({"name": "x", "ceo": "y"}, {"name": 5}, {"name": "x" * 301}, "Example Kft."):
        assert http.put("/admin/settings/operator", json={"value": wrong}).status_code == 400, wrong


def test_the_cookie_policy_names_everything_the_site_keeps_on_a_device():
    """The relationship, not a list: every cookie the API sets, every key the site writes to the browser's
    storage and the service worker it registers is in the policy's table. "install-later" was kept on phones
    for a day while the policy did not name it."""
    from libs.auth.sessions import ACCESS_COOKIE, REFRESH_COOKIE
    from libs.legal.disclosure import STORED

    named = {part.strip() for stored in STORED for part in stored.name.split(",")}

    set_by_the_api = {name for path in (ROOT / "services").rglob("*.py")
                      for name in re.findall(r"\.set_cookie\(\s*(\w+)", path.read_text(encoding="utf-8"))}
    assert set_by_the_api == {"ACCESS_COOKIE", "REFRESH_COOKIE"}, "a cookie set under a name of its own"
    assert {ACCESS_COOKIE, REFRESH_COOKIE} <= named

    written, workers = set(), set()
    for path in [*(ROOT / "web" / "src").rglob("*.ts*"), ROOT / "web" / "index.html"]:
        text = path.read_text(encoding="utf-8")
        constants = dict(re.findall(r'const (\w+) = "([^"]+)"', text))
        for key in re.findall(r'(?:localStorage|sessionStorage)\.setItem\(\s*(\w+|"[^"]+")', text):
            written.add(key.strip('"') if key.startswith('"') else constants[key])
        workers |= set(re.findall(r'serviceWorker\.register\("/([\w.-]+)"\)', text))
    assert written and workers, "the scan found nothing: it no longer reads the site"
    unnamed = (written | workers) - named
    assert not unnamed, f"kept on a device, and not in the Cookie Policy: {unnamed}"


def test_messages_are_deleted_when_the_privacy_policy_says(api):
    """The database deletes a message when the policy says it goes, so the two cannot disagree."""
    from libs.db.models import ContactMessage
    from libs.db.models.contact import CONTACT_KEEP_YEARS

    http, db = api
    expiring = [index for index in db[ContactMessage.COLLECTION].index_information().values()
                if index["key"] == [("created_at", 1)] and "expireAfterSeconds" in index]
    assert [index["expireAfterSeconds"] for index in expiring] == [CONTACT_KEEP_YEARS * 366 * 86400]
    assert f"Up to {CONTACT_KEEP_YEARS} years" in http.get("/legal/privacy").json()["markdown"]
