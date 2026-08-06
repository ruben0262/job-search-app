"""Engine and session factory."""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def database_url() -> str:
    """Read ``DATABASE_URL``, failing loudly rather than defaulting to something plausible."""
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
    """A transactional scope."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
