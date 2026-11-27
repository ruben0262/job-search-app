"""The site's places on social networks (owner, 2026-11-18): "we need to add socials to the site in
general ... so users can join and share their thoughts as well as their requests to the devs". The owner's
Discord is there from the start, for anybody, and since 2026-11-23 the site's Facebook, TikTok and Instagram;
the admin changes or empties each, and only a web address is kept."""
from __future__ import annotations

import os

import pytest

from libs.db import insert
from libs.db.models import User
from libs.settings import DEFAULT_SOCIALS, SOCIAL_NETWORKS

ADMIN = "operator@example.com"
# The owner's links (`libs.settings`), the others in the order the foot shows them.
DISCORD = DEFAULT_SOCIALS["discord"]
OTHERS = [{"network": network, "url": DEFAULT_SOCIALS[network]}
          for network in SOCIAL_NETWORKS if network != "discord"]


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
    from libs.db import database
    from tests.conftest import access_cookie
    http.cookies.set("access_token", access_cookie(database(), user.id), path="/")


def test_the_discord_is_there_for_anybody_and_the_admin_changes_or_hides_it(db, http):
    signed_out = http.get("/socials").json()
    assert signed_out == [{"network": "discord", "url": DISCORD}, *OTHERS], "for anybody, from the start"

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
    assert http.get("/socials").json() == [{"network": "discord", "url": moved}, *OTHERS], "the others kept"

    _sign_in(http, admin)
    emptied = {"discord": "", "tiktok": ""}
    assert http.put("/admin/settings/socials", json={"value": emptied}).status_code == 200
    assert http.get("/socials").json() == [other for other in OTHERS if other["network"] != "tiktok"], \
        "emptied, it is not shown"


def test_a_network_added_after_the_links_were_saved_shows_all_the_same(db, http):
    """Saved in Admin while Discord was the only network, the setting holds no line for the three added on
    2026-11-23: each shows with the owner's link, on the site and in Admin alike."""
    from libs import settings

    settings.put(db, "socials", {"discord": DISCORD})
    assert http.get("/socials").json() == [{"network": "discord", "url": DISCORD}, *OTHERS]
    _sign_in(http, _person(db, ADMIN))
    assert http.get("/admin/settings").json()["settings"]["socials"] == {
        "discord": DISCORD, **{other["network"]: other["url"] for other in OTHERS}}
