"""Engine and session factory. Synchronous, psycopg3.

**Why sync.** The harvester, trainer, notifier, and docgen are sequential workers; the queue's claim
protocol is a single ``UPDATE ... FOR UPDATE SKIP LOCKED`` that reads far more clearly without
``await`` on every line. FastAPI runs sync dependencies in a threadpool, which is comfortable below
the ~50 users this design targets. It is the same reasoning that rejected Redis: pick the mechanism
whose failure modes you can see, and revisit when a measurement says to.

**Connection budget.** Postgres is the single point of failure and every service holds a pool
against it. ``mem_limit: 4g`` on the container and roughly eight services means pool sizes are a
platform-wide budget, not a per-service preference — hence the conservative defaults here rather
than SQLAlchemy's.

``pool_pre_ping`` is on deliberately: the platform's workers idle for minutes between jobs, and a
connection killed by a restart or a network blip otherwise surfaces as a failed job rather than a
transparent reconnect.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def database_url() -> str:
    """Read ``DATABASE_URL``, failing loudly rather than defaulting to something plausible.

    A default like ``postgresql://localhost/jobsearch`` is worse than no default: it turns a missing
    configuration into a confusing connection error against the wrong database, and in the worst case
    a successful connection to a *developer's* database from a production container.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Expected e.g. "
            "postgresql+psycopg://jobsearch:<password>@postgres:5432/jobsearch")
    return url


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "5")),
            pool_pre_ping=True,
            pool_recycle=1800,
            future=True,
        )
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional scope. Commits on success, rolls back on any exception.

    Workers use this per job, so that the unit of work and the unit of retry are the same thing —
    which is what makes "claimed transactionally, expired claim returns to the queue" true rather
    than aspirational (SCHEDULER_PLAN.md §4).
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
