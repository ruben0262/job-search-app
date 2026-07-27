"""Declarative base plus the three conventions every table inherits.

These are set once, here, rather than per-model — each of them is cheap now and a migration later.

**1. Constraint naming.** Postgres auto-names constraints, Alembic emits ``ALTER ... DROP CONSTRAINT
<name>``, and autogenerate cannot name something it did not create. Without a convention the first
constraint change produces a migration that fails on the server and works on nobody's machine. The
convention below makes every name deterministic from the table and column.

**2. ``TIMESTAMPTZ`` everywhere, never naive** (GAPS_REVIEW G7, TENANT_SAFETY.md §3). Users are
across Europe and internationally; per-day quota counters reset at *their* local midnight, not UTC's.
A naive column silently makes that impossible to compute correctly, and the bug shows up as "my daily
limit reset at a weird time" months after launch. The ``timestamptz`` annotation below is the only
timestamp type any model should use.

**3. Enums as ``TEXT`` + ``CHECK``, not native Postgres ``ENUM``.** Adding a value to a native enum
is a migration that cannot run inside a transaction with other DDL on older servers, and removing one
is worse. A CHECK constraint is edited by dropping and recreating it, which Alembic handles cleanly.
The type safety we actually want lives in the Python layer either way.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Every timestamp column in the platform. Annotate with this, never with bare `datetime`.
timestamptz = Annotated[datetime, mapped_column(DateTime(timezone=True))]

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for every platform table.

    ``metadata`` here is what ``alembic/env.py`` compares the live database against, so a model that
    is not imported by ``libs.db.models`` is invisible to autogenerate and will silently never get a
    migration. That is the one failure mode of this setup worth remembering.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # RUF012 is suppressed below: SQLAlchemy reads this as a plain class attribute, and annotating
    # it ClassVar as the lint suggests is not what the declarative API expects.
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012
