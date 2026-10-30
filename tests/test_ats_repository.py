"""ATS harvester state: tokens, and the ETag cache that makes polling near-free."""
from __future__ import annotations

import uuid

import pytest
from pymongo.errors import DuplicateKeyError

from libs.corpus.ats import AtsRepository
from libs.db.models import AtsEtag, AtsToken


@pytest.fixture()
def r(db):
    return AtsRepository(db)


def _ats_name() -> str:
    return f"gh_{uuid.uuid4().hex[:10]}"


# ── tokens ────────────────────────────────────────────────────────────────────────────────────

def test_tokens_are_deduplicated_and_counted_honestly(r):
    name = _ats_name()
    assert r.add_tokens([(name, "acme"), (name, "globex")]) == 2
    # Re-harvesting an existing company must be free and must not inflate the "new" count.
    assert r.add_tokens([(name, "acme"), (name, "initech")]) == 1
    assert sorted(r.live_tokens(name)) == ["acme", "globex", "initech"]


def test_tokens_are_case_normalised(r):
    """Every ATS API is case-insensitive on the token, so three spellings are one company."""
    name = _ats_name()
    added = r.add_tokens([(name, "Acme"), (name, "ACME"), (name.upper(), " acme ")])
    assert added == 1, "Acme / ACME / ' acme ' are the same board"
    assert r.live_tokens(name) == ["acme"]


def test_a_dead_board_stops_being_polled_but_is_not_forgotten(r):
    """Deleting it would let the next stale apply URL re-harvest and re-poll the same 404."""
    name = _ats_name()
    r.add_tokens([(name, "acme"), (name, "globex")])

    r.mark_dead(name, "globex")
    assert r.live_tokens(name) == ["acme"]

    # Not forgotten: re-harvesting it adds nothing, and does not bring it back to life either.
    assert r.add_tokens([(name, "globex")]) == 0
    assert r.live_tokens(name) == ["acme"]

    r.revive(name, "globex")
    assert sorted(r.live_tokens(name)) == ["acme", "globex"]


def test_one_board_is_one_document_whoever_writes_it(db):
    """The guarantee `add_tokens` and `set_etag` lean on: a second writer cannot add a twin."""
    db[AtsToken.COLLECTION].insert_one({"ats": "greenhouse", "token": "acme"})
    with pytest.raises(DuplicateKeyError):
        db[AtsToken.COLLECTION].insert_one({"ats": "greenhouse", "token": "acme"})
    db[AtsEtag.COLLECTION].insert_one({"ats": "greenhouse", "token": "acme", "etag": "a"})
    with pytest.raises(DuplicateKeyError):
        db[AtsEtag.COLLECTION].insert_one({"ats": "greenhouse", "token": "acme", "etag": "b"})


# ── etags ─────────────────────────────────────────────────────────────────────────────────────

def test_etags_round_trip_in_the_shape_the_poll_loop_expects(r):
    name = _ats_name()
    r.set_etag(name, "acme", 'W/"abc123"')
    r.set_etag(name, "globex", 'W/"def456"')
    assert r.etags(name) == {"acme": 'W/"abc123"', "globex": 'W/"def456"'}


def test_a_304_keeps_the_validator(r):
    """A 304 means "unchanged, your validator is still good"."""
    name = _ats_name()
    r.add_tokens([(name, "acme")])
    r.set_etag(name, "acme", 'W/"abc123"')

    r.record_poll(name, "acme", etag='W/"abc123"', alive=True)
    assert r.etags(name) == {"acme": 'W/"abc123"'}


def test_a_live_board_with_no_etag_does_not_clear_an_existing_one(r):
    """Some ATS (Ashby, Recruitee, Workable v1) do not send ETags at all."""
    name = _ats_name()
    r.set_etag(name, "acme", 'W/"abc123"')

    r.record_poll(name, "acme", etag=None, alive=True)
    assert r.etags(name) == {"acme": 'W/"abc123"'}


def test_a_dead_board_forgets_its_validator(r):
    """So a board that comes back is fetched in full."""
    name = _ats_name()
    r.add_tokens([(name, "acme")])
    r.set_etag(name, "acme", 'W/"abc123"')

    r.record_poll(name, "acme", etag=None, alive=False)
    assert r.etags(name) == {}
    assert r.live_tokens(name) == []


def test_concurrent_writers_cannot_clobber_each_other(r):
    """The whole reason this store became a collection rather than a file."""
    name = _ats_name()
    for i in range(50):
        r.set_etag(name, f"company{i}", f'W/"etag{i}"')

    # Interleave an update to one row with fresh inserts, as two overlapping gathers would.
    r.set_etag(name, "company7", 'W/"updated"')
    for i in range(50, 60):
        r.set_etag(name, f"company{i}", f'W/"etag{i}"')

    got = r.etags(name)
    assert len(got) == 60, "no writer's keys may be lost"
    assert got["company7"] == 'W/"updated"'
    assert got["company0"] == 'W/"etag0"'
    assert got["company59"] == 'W/"etag59"'
