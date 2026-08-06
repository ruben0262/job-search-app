"""Declarative base plus the three conventions every table inherits."""
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
    """Base for every platform table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # RUF012 suppressed: SQLAlchemy reads this as a plain class attribute, not a ClassVar.
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012
