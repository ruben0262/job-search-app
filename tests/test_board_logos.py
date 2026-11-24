"""Every source the harvest reads is on the landing page's strip, its logo beside its name (owner,
2026-11-24): a source added without a logo would show a broken picture there, and a logo kept for a source no
longer read would never be seen."""
from __future__ import annotations

import os
import pathlib
import struct

import pytest

from libs.corpus.boards import board_name
from libs.settings import HARVEST_SOURCES

BOARDS = pathlib.Path(__file__).resolve().parent.parent / "web" / "public" / "boards"
PNG = b"\x89PNG\r\n\x1a\n"


def test_every_source_the_harvest_reads_has_its_logo_and_no_other_has_one():
    for key in HARVEST_SOURCES:
        raw = (BOARDS / f"{key}.png").read_bytes() if (BOARDS / f"{key}.png").exists() else b""
        assert raw[:8] == PNG, f"{key} has no logo in web/public/boards"
        # The size the strip asks for, at twice its 32px and more: the PNG's own header says it.
        assert struct.unpack(">II", raw[16:24]) == (128, 128), key
    kept = sorted(logo.stem for logo in BOARDS.glob("*.png"))
    assert kept == sorted(HARVEST_SOURCES), "a logo nothing shows, or a source without one"


@pytest.fixture()
def http(db, monkeypatch):
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    from fastapi.testclient import TestClient

    from services.api.app import app
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_the_landing_page_is_told_every_source_by_name_in_the_harvests_order(http):
    """Asked before anybody has an account, the landing page's one list (`/stats`): the strip draws it."""
    body = http.get("/stats").json()
    # How often the sites are read is the owner's to keep (2026-11-17: "confidential"): nothing else served.
    assert set(body) == {"boards", "ats"}
    boards = body["boards"]
    assert boards == [{"key": key, "name": board_name(key)} for key in HARVEST_SOURCES]
    assert {"LinkedIn", "Greenhouse", "Workday", "Remote OK"} <= {board["name"] for board in boards}
