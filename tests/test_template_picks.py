"""Picking a look and a colour, through the API: what the picker is sent, what a pick keeps, what it refuses.

Apart from `test_templates.py`, which draws every look as a PDF and needs none of the API: a change to the API
reaches these five and not those hundred and fifty prints (`tests/affected.py`).
"""
from __future__ import annotations

import pytest

from libs import templates

pytest.importorskip("fastapi")


@pytest.fixture()
def http(db, user, running, monkeypatch):
    from tests.conftest import access_cookie

    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    running.cookies.clear()
    running.cookies.set("access_token", access_cookie(db, user.id), path="/")
    return running


def test_the_picker_shows_the_most_popular_then_each_sector_and_the_look_in_use(http):
    shelves = http.get("/templates").json()
    for kind in ("cv", "cover"):
        shelf = shelves[kind]
        assert shelf["chosen"] == "meridian"
        first = shelf["groups"][0]
        assert first["title"] and first["templates"] == list(templates.POPULAR[kind])
        assert len(shelf["groups"]) == 1 + len(templates.SECTORS)
        named = {card["id"]: card for card in shelf["templates"]}
        # A name and a picture for each, and nothing else: the descriptions went (owner, 2026-11-07). The
        # pictures come with the shelf, so the sheet draws them without asking for each (2026-11-08).
        assert all(set(named[id_]) == {"id", "name", "picture"} and named[id_]["name"]
                   and named[id_]["picture"] == templates.picture(templates.find(kind, id_))
                   for group in shelf["groups"] for id_ in group["templates"])
    http.cookies.clear()
    assert http.get("/templates").status_code == 401


def test_a_pick_is_kept_for_its_own_kind_only(http, db, user):
    from libs.db import get
    from libs.db.models import User

    answer = http.put("/templates/cv", json={"id": "ledger"})
    assert answer.status_code == 200
    assert answer.json()["cv"]["chosen"] == "ledger" and answer.json()["cover"]["chosen"] == "meridian"
    assert http.get("/templates").json()["cv"]["chosen"] == "ledger"
    assert get(db, User, user.id).cv_template == "ledger"


def test_a_look_that_does_not_exist_is_refused_and_nothing_changes(http):
    assert http.put("/templates/cover", json={"id": "quill"}).status_code == 404, "Quill is a CV look only"
    assert http.put("/templates/cv", json={"id": "nope"}).status_code == 404
    assert http.put("/templates/letter", json={"id": "meridian"}).status_code == 422
    assert http.get("/templates").json()["cover"]["chosen"] == "meridian"


def test_one_colour_is_kept_for_both_kinds_and_every_picture_is_drawn_in_it(http, db, user):
    """Picked beside the looks (owner, 2026-11-17: one colour for the CV and the cover letter), so every
    picture shows what the next document will look like; none picked again, each look is its own."""
    from libs.db import get
    from libs.db.models import User

    shelves = http.get("/templates").json()
    assert shelves["colour"] is None
    assert [each["id"] for each in shelves["colours"]] == list(templates.COLOURS)
    navy = templates.COLOURS["navy"][1]
    answer = http.put("/templates/colour", json={"id": "navy"})
    assert answer.status_code == 200 and answer.json()["colour"] == "navy"
    assert get(db, User, user.id).look_colour == "navy"
    for kind in ("cv", "cover"):
        cards = answer.json()[kind]["templates"]
        assert cards and all(navy in card["picture"] for card in cards), f"a {kind} picture is not in navy"
        assert answer.json()[kind]["chosen"] == "meridian", "a colour changes no look"
    back = http.put("/templates/colour", json={"id": None}).json()
    assert back["colour"] is None
    assert all(navy not in card["picture"] for card in back["cv"]["templates"])


def test_a_colour_that_does_not_exist_is_refused_and_nothing_changes(http):
    assert http.put("/templates/colour", json={"id": "teal"}).status_code == 200
    assert http.put("/templates/colour", json={"id": "beige"}).status_code == 404
    assert http.get("/templates").json()["colour"] == "teal"
