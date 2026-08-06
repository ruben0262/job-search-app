"""Test-wide setup."""
from __future__ import annotations

try:
    import sqlalchemy
    from sqlalchemy.pool import NullPool
except ImportError:  # pragma: no cover - the non-DB tests run without SQLAlchemy installed
    sqlalchemy = None

if sqlalchemy is not None:
    _create_engine = sqlalchemy.create_engine

    def _unpooled(*args, **kwargs):
        # An explicit `poolclass` still wins, so a test exercising pooling can ask for it.
        kwargs.setdefault("poolclass", NullPool)
        return _create_engine(*args, **kwargs)

    sqlalchemy.create_engine = _unpooled
