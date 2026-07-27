"""Every table in the platform, re-exported so Alembic can see all of them.

**This module is load-bearing for migrations.** ``alembic/env.py`` compares the live database against
``Base.metadata``, and a model class that is never imported is never registered on that metadata — so
it produces no migration, no table, and no error. The failure is silent and shows up as a missing
relation at runtime. Any new model must be imported here.

The split by file is by ownership, not by convenience:

- ``user`` — the tenant: account, scheduling state, config, credentials
- ``corpus`` — the shared job corpus. The only table that is not per-user, and must stay that way
- ``tenant`` — the per-user tail: scores, seen/notified state, labels, model artifacts
- ``queue`` — the four-lane work queue
"""
from __future__ import annotations

from libs.db.base import Base
from libs.db.models.corpus import CorpusJob
from libs.db.models.queue import CLAIM_TIMEOUT_MIN, JOB_STATES, LANES, MAX_ATTEMPTS, QueueJob
from libs.db.models.tenant import LABEL_SOURCES, UserJobState, UserLabel, UserModel
from libs.db.models.user import (
    SECRET_KINDS,
    TELEGRAM_STATUSES,
    USER_STATUSES,
    User,
    UserConfig,
    UserSecret,
)

__all__ = [
    "CLAIM_TIMEOUT_MIN",
    "JOB_STATES",
    "LABEL_SOURCES",
    "LANES",
    "MAX_ATTEMPTS",
    "SECRET_KINDS",
    "TELEGRAM_STATUSES",
    "USER_STATUSES",
    "Base",
    "CorpusJob",
    "QueueJob",
    "User",
    "UserConfig",
    "UserJobState",
    "UserLabel",
    "UserModel",
    "UserSecret",
]
