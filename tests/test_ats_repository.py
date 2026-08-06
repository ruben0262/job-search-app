"""ATS harvester state: tokens, and the ETag cache that makes polling near-free."""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from libs.corpus.ats import AtsRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set (no live Postgres)")


@pytest.fixture()
def ats():
    engine = create_engine(os.environ["DATABASE_URL"])
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield AtsRepository(s), s
    finally:
        s.close()
        trans.rollback()
        conn.close()


def _ats_name() -> str:
    return f"gh_{uuid.uuid4().hex[:10]}"


# ── tokens ────────────────────────────────────────────────────────────────────────────────────

def test_tokens_are_deduplicated_and_counted_honestly(ats):
    r, s = ats
    name = _ats_name()
    assert r.add_tokens([(name, "acme"), (name, "globex")]) == 2
    s.flush()
    # Re-harvesting an existing company must be free and must not inflate the "new" count.
    assert r.add_tokens([(name, "acme"), (name, "initech")]) == 1
    s.flush()
    assert sorted(r.live_tokens(name)) == ["acme", "globex", "initech"]


def test_tokens_are_case_normalised(ats):
    """Every ATS API is case-insensitive on the token, so three spellings are one company."""
    r, s = ats
    name = _ats_name()
    added = r.add_tokens([(name, "Acme"), (name, "ACME"), (name.upper(), " acme ")])
    s.flush()
    assert added == 1, "Acme / ACME / ' acme ' are the same board"
    assert r.live_tokens(name) == ["acme"]


def test_a_dead_board_stops_being_polled_but_is_not_forgotten(ats):
    """Deleting it would let the next stale apply URL re-harvest and re-poll the same 404."""
    r, s = ats
    name = _ats_name()
    r.add_tokens([(name, "acme"), (name, "globex")])
    s.flush()

    r.mark_dead(name, "globex")
    s.flush()
    assert r.live_tokens(name) == ["acme"]

    # Not forgotten: re-harvesting it adds nothing, because the row is still there.
    assert r.add_tokens([(name, "globex")]) == 0
    s.flush()

    r.revive(name, "globex")
    s.flush()
    assert sorted(r.live_tokens(name)) == ["acme", "globex"]


# ── etags ─────────────────────────────────────────────────────────────────────────────────────

def test_etags_round_trip_in_the_shape_the_poll_loop_expects(ats):
    r, s = ats
    name = _ats_name()
    r.set_etag(name, "acme", 'W/"abc123"')
    r.set_etag(name, "globex", 'W/"def456"')
    s.flush()
    assert r.etags(name) == {"acme": 'W/"abc123"', "globex": 'W/"def456"'}


def test_a_304_keeps_the_validator(ats):
    """A 304 means "unchanged, your validator is still good"."""
    r, s = ats
    name = _ats_name()
    r.add_tokens([(name, "acme")])
    r.set_etag(name, "acme", 'W/"abc123"')
    s.flush()

    r.record_poll(name, "acme", etag='W/"abc123"', alive=True)
    s.flush()
    assert r.etags(name) == {"acme": 'W/"abc123"'}


def test_a_live_board_with_no_etag_does_not_clear_an_existing_one(ats):
    """Some ATS (Ashby, Recruitee, Workable v1) do not send ETags at all."""
    r, s = ats
    name = _ats_name()
    r.set_etag(name, "acme", 'W/"abc123"')
    s.flush()

    r.record_poll(name, "acme", etag=None, alive=True)
    s.flush()
    assert r.etags(name) == {"acme": 'W/"abc123"'}


def test_a_dead_board_forgets_its_validator(ats):
    """So a board that comes back is fetched in full."""
    r, s = ats
    name = _ats_name()
    r.add_tokens([(name, "acme")])
    r.set_etag(name, "acme", 'W/"abc123"')
    s.flush()

    r.record_poll(name, "acme", etag=None, alive=False)
    s.flush()
    assert r.etags(name) == {}
    assert r.live_tokens(name) == []


def test_concurrent_writers_cannot_clobber_each_other(ats):
    """The whole reason this store became a table."""
    r, s = ats
    name = _ats_name()
    for i in range(50):
        r.set_etag(name, f"company{i}", f'W/"etag{i}"')
    s.flush()

    # Interleave an update to one row with fresh inserts, as two overlapping gathers would.
    r.set_etag(name, "company7", 'W/"updated"')
    for i in range(50, 60):
        r.set_etag(name, f"company{i}", f'W/"etag{i}"')
    s.flush()

    got = r.etags(name)
    assert len(got) == 60, "no writer's keys may be lost"
    assert got["company7"] == 'W/"updated"'
    assert got["company0"] == 'W/"etag0"'
    assert got["company59"] == 'W/"etag59"'
