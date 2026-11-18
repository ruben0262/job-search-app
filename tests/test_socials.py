"""The site's places on social networks (owner, 2026-11-18): "we need to add socials to the site in
general ... so users can join and share their thoughts as well as their requests to the devs". The owner's
Discord is there from the start, for anybody; the admin changes it or empties it, and only a web address is
kept."""
from __future__ import annotations

import os
import uuid

import pytest

from libs.db import insert
from libs.db.models import User

ADMIN = "operator@example.com"
DISCORD = "https://discord.gg/UR3FNC4gyT"


@pytest.fixture()
def http(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)

    from fastapi.testclient import TestClient

    from services.api.app import app
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _person(db, email: str) -> User:
    return insert(db, User(email=email, password_hash="x", status="active", active=True))


def _sign_in(http, user: User) -> None:
    from libs.auth.access import issue_access_token
    http.cookies.set("access_token", issue_access_token(user.id, uuid.uuid4()), path="/")


def test_the_discord_is_there_for_anybody_and_the_admin_changes_or_hides_it(db, http):
    signed_out = http.get("/socials").json()
    assert signed_out == [{"network": "discord", "url": DISCORD}], "for anybody, from the start"

    admin = _person(db, ADMIN)
    _sign_in(http, _person(db, "someone@example.com"))
    # Not an admin: the admin's routes are not there for them.
    assert http.put("/admin/settings/socials", json={"value": {"discord": ""}}).status_code == 404

    _sign_in(http, admin)
    for wrong in ({"discord": "discord.gg/abc"}, {"discord": "http://discord.gg/abc"},
                  {"discord": "javascript:alert(1)"}, {"myspace": "https://myspace.com/x"}, "https://discord.gg/x"):
        assert http.put("/admin/settings/socials", json={"value": wrong}).status_code == 400, wrong
    moved = "https://discord.com/invite/mahoraga"
    assert http.put("/admin/settings/socials", json={"value": {"discord": f" {moved} "}}).status_code == 200
    http.cookies.clear()
    assert http.get("/socials").json() == [{"network": "discord", "url": moved}]

    _sign_in(http, admin)
    assert http.put("/admin/settings/socials", json={"value": {"discord": ""}}).status_code == 200
    assert http.get("/socials").json() == [], "emptied, it is not shown"
