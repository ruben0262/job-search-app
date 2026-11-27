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


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--affected", nargs="?", const="HEAD", default=None, metavar="REV",
        help="run only the test files that what changed since REV can reach (tests/affected.py). "
             "REV is HEAD when left out: the work not yet committed.")


def _affected(config) -> tuple[list[str], str | None, list[str]] | None:
    """What `--affected` picked, worked out once a process: every worker picks the same from the same tree."""
    if config.getoption("affected") is None:
        return None
    if not hasattr(config, "_affected"):
        from tests import affected
        config._affected = affected.reached(affected.changed(config.getoption("affected")))
    return config._affected


def pytest_ignore_collect(collection_path, config) -> bool | None:
    picked = _affected(config)
    if picked is None or not collection_path.name.startswith("test_") or collection_path.suffix != ".py":
        return None
    from tests.affected import ROOT
    return collection_path.relative_to(ROOT).as_posix() not in picked[0] or None


def pytest_terminal_summary(terminalreporter, config) -> None:
    """Said beside the counts, however quiet the run (`-q` prints no header): a short run is not taken for the
    whole suite, nor a file no test reaches for a tested one."""
    picked = _affected(config)
    if picked is None:
        return
    from tests import affected
    files, everything, unreached = picked
    terminalreporter.write_line(
        f"affected: {len(files)} of {len(affected.tests())} test files, by what changed since "
        f"{config.getoption('affected')}" + (f" ({everything})" if everything else ""))
    if unreached:
        terminalreporter.write_line(f"no test reaches: {', '.join(unreached)}")


def pytest_sessionfinish(session, exitstatus) -> None:
    """A change no test can reach has nothing to run: a pass, and not pytest's "no tests collected"."""
    picked = _affected(session.config)
    if picked is not None and not picked[0] and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK


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


@pytest.fixture(scope="module")
def running(_database):
    """The real API, started once for a file's tests and not once a test: each start lays the whole schema
    again (`services.api.app.lifespan`), which was most of what a short test took. Asked over https, as its
    cookies are."""
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    from fastapi.testclient import TestClient

    from services.api.app import app
    with TestClient(app, base_url="https://testserver") as http:
        yield http


def agreed() -> dict:
    """What ticking sign-up's box records (`libs.legal`): an account a test makes is one that signed up."""
    from libs.db import now
    from libs.legal import LEGAL_VERSION
    return {"processing_consent_at": now(), "processing_consent_version": LEGAL_VERSION}


def access_cookie(db, user_id) -> str:
    """The access cookie of a browser this account is signed in on: a token for a session it has. A token
    alone is not signed in: every request asks whether its session was ended (`services.api.deps`)."""
    from libs.auth.access import issue_access_token
    from libs.auth.sessions import issue_session
    return issue_access_token(user_id, issue_session(db, user_id).session_id)


@pytest.fixture()
def user(db):
    """One active account, with nothing else."""
    import uuid

    from libs.db import insert
    from libs.db.models import User
    return insert(db, User(email=f"{uuid.uuid4().hex[:12]}@example.com", password_hash="x",
                           subscription_status="active",
                           status="active", active=True, **agreed()))
