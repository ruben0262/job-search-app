"""Test-wide setup: one MongoDB database for the run, emptied after every test that used it.

**Never Atlas.** The tests empty every collection they touch, so they run only against a server the
developer started for them (`MONGODB_URI`, e.g. a local `mongod --replSet rs0`), and only in a
database named `test_<pid>`. A URI that names an Atlas cluster is refused outright: the platform's own
credentials sit in `certificates/`, one `export` away from a test run.
"""
from __future__ import annotations

import os

import pytest


def _test_uri() -> str | None:
    uri = os.environ.get("MONGODB_URI")
    if uri and ".mongodb.net" in uri:
        raise pytest.UsageError("MONGODB_URI names an Atlas cluster. The tests delete data; run them "
                                "against a local mongod (see tests/README.md), never the platform's.")
    return uri


needs_db = pytest.mark.skipif(not os.environ.get("MONGODB_URI"),
                              reason="MONGODB_URI not set (no MongoDB to test against)")


@pytest.fixture(scope="session")
def _database():
    uri = _test_uri()
    if not uri:
        pytest.skip("MONGODB_URI not set (no MongoDB to test against)")
    import libs.db
    from libs.db import database, ensure_schema

    os.environ["MONGODB_DB"] = f"test_{os.getpid()}"
    libs.db._client = None
    db = database()
    db.client.drop_database(db.name)
    ensure_schema(db)
    yield db
    db.client.drop_database(db.name)


@pytest.fixture()
def db(_database):
    """The test database, empty. Everything a test writes is gone before the next one starts."""
    yield _database
    for name in _database.list_collection_names():
        _database[name].delete_many({})


@pytest.fixture()
def user(db):
    """One active account, with nothing else."""
    import uuid

    from libs.db import insert
    from libs.db.models import User
    return insert(db, User(email=f"{uuid.uuid4().hex[:12]}@example.com", password_hash="x",
                           status="active", active=True))
