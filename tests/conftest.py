"""Test-wide setup. Currently one thing: stop the suite exhausting Postgres's connection slots.

**The problem.** Every DB-backed fixture builds its own `create_engine(...)`, and a function-scoped
fixture therefore builds one per test. SQLAlchemy's default `QueuePool` returns a connection to the
pool on `conn.close()` rather than to the server, and the engine is never disposed — so each test
leaves one idle connection open and a few hundred tests exhaust the default `max_connections` of
100. The symptom is not a clear error either: the run gets most of the way through and then a
handful of *unrelated* tests fail at setup with "sorry, too many clients already", which reads like
flakiness in whichever tests happened to be last.

**Why here rather than at 69 call sites.** Disposing each engine would work and would be 69 edits
that the next new test can forget. Pooling buys a test suite nothing — every fixture opens exactly
one connection and holds it for one test — so turning the default off is both the smaller change
and the more correct one.

**Why at import time rather than in a fixture.** `conftest.py` is imported before the test modules
are collected, but fixtures do not run until the first test — by which point any module that did
`from sqlalchemy import create_engine` at its top level has already bound the original. Patching
here, on import, is the only point that catches all of them.

Nothing in `libs` or `services` is affected: production wants pooling, and this module is never
imported outside a test run.
"""
from __future__ import annotations

try:
    import sqlalchemy
    from sqlalchemy.pool import NullPool
except ImportError:  # pragma: no cover - the non-DB tests run without SQLAlchemy installed
    sqlalchemy = None

if sqlalchemy is not None:
    _create_engine = sqlalchemy.create_engine

    def _unpooled(*args, **kwargs):
        # An explicit `poolclass` still wins, so a test that deliberately exercises pooling can.
        kwargs.setdefault("poolclass", NullPool)
        return _create_engine(*args, **kwargs)

    sqlalchemy.create_engine = _unpooled
