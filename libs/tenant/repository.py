"""Per-user repository: job state, applied log, and labels."""
from __future__ import annotations

import uuid as _uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.contracts.config import CONFIG_SCHEMA_VERSION
from libs.contracts.profile import PROFILE_SCHEMA_VERSION
from libs.db.models import (
    CorpusJob,
    ProxyCredential,
    QueueJob,
    User,
    UserConfig,
    UserJobState,
    UserLabel,
    UserModel,
    UserSecret,
)

if TYPE_CHECKING:  # Runtime import lives inside the method, to avoid a cycle.
    pass

# Current plus one previous (GAPS_REVIEW G12).
KEEP_MODEL_VERSIONS = 2


@dataclass(frozen=True)
class SecretMetadata:
    """What a user may see about their own credential. Deliberately no value field."""

    kind: str
    last_4: str | None
    validated_at: object
    updated_at: object


@dataclass(frozen=True)
class AppliedRecord:
    """One row of the applied log, joined to the corpus for display."""

    job_id: int
    url: str
    title: str
    company: str | None
    applied_at: object
    cover_built: bool


class TenantRepository:
    """All per-user reads and writes."""

    def __init__(self, session: Session):
        self._s = session

    # ── job state ────────────────────────────────────────────────────────────────────────────

    def _state(self, user_id: _uuid.UUID, job_id: int) -> UserJobState:
        """Get or create this user's row for this job. Never shared, never implicit."""
        row = self._s.get(UserJobState, (user_id, job_id))
        if row is None:
            row = UserJobState(user_id=user_id, job_id=job_id)
            self._s.add(row)
            self._s.flush()
        return row

    def record_score(self, user_id: _uuid.UUID, job_id: int, *, score: float,
                     model_version: int) -> None:
        """Store a fit score and which model produced it."""
        row = self._state(user_id, job_id)
        row.score = score
        row.scored_at = func.now()
        row.scored_model_version = model_version

    def record_applied(self, user_id: _uuid.UUID, job_id: int, *,
                       cover_built: bool = False) -> bool:
        """Log that this user built a CV for this job."""
        row = self._state(user_id, job_id)
        is_new = row.applied_at is None
        if is_new:
            row.applied_at = func.now()
        if cover_built:
            row.cover_built = True
        return is_new

    def applied(self, user_id: _uuid.UUID, limit: int = 200) -> list[AppliedRecord]:
        """This user's applied log, most recent first. Joined to the corpus for title/company."""
        rows = self._s.execute(
            select(UserJobState.job_id, CorpusJob.url, CorpusJob.title, CorpusJob.company,
                   UserJobState.applied_at, UserJobState.cover_built)
            .join(CorpusJob, CorpusJob.id == UserJobState.job_id)
            .where(UserJobState.user_id == user_id, UserJobState.applied_at.is_not(None))
            .order_by(UserJobState.applied_at.desc())
            .limit(limit))
        return [AppliedRecord(*r) for r in rows]

    def applied_count(self, user_id: _uuid.UUID) -> int:
        return self._s.scalar(
            select(func.count())
            .select_from(UserJobState)
            .where(UserJobState.user_id == user_id, UserJobState.applied_at.is_not(None))) or 0

    # ── config ───────────────────────────────────────────────────────────────────────────────

    def get_config(self, user_id: _uuid.UUID) -> dict | None:
        """This user's config, or `None` if they have not completed one yet."""
        return self._s.scalar(
            select(UserConfig.config).where(UserConfig.user_id == user_id))

    def save_config(self, user_id: _uuid.UUID, config: dict, *,
                    schema_version: int = CONFIG_SCHEMA_VERSION) -> None:
        """Replace this user's config."""
        self._s.execute(
            pg_insert(UserConfig)
            .values(user_id=user_id, config=config, schema_version=schema_version)
            .on_conflict_do_update(
                index_elements=[UserConfig.user_id],
                set_={"config": config, "schema_version": schema_version,
                      "updated_at": func.now()}))

    # ── captured profile ─────────────────────────────────────────────────────────────────────

    def save_profile(self, user_id: _uuid.UUID, profile: dict, *,
                     schema_version: int = PROFILE_SCHEMA_VERSION) -> int:
        """Store a confirmed profile. **Never touches the rubric.**

        `label.md` is the rubric, for every account, with no second path. It is in `SEED_FILES`, so
        every account is created with one, and the setup agent mirrors it onto the row as it writes
        it (`libs/agents/tools.py::_sync_rubric`). One mechanism, one owner.

        It used to be two: this method also rendered a rubric from the confirmed schema and wrote
        that instead, which meant storing a profile could silently replace a person's own tuned
        file with a generated summary — and bump `profile_generation`, staling every label they had
        already bought and charging them to buy the lot again. Measured on one account: a 13,809
        character `label.md` against 320 labels.

        Storing the profile is what makes documents buildable, and that is all it does now. The
        generation is not bumped either: nothing here changes what a label means.
        """
        if not profile.get("confirmed"):
            raise ValueError("refusing to store an unconfirmed profile — see PROFILE_CAPTURE_PLAN §5")
        user = self._s.get(User, user_id)
        if user is None:
            raise ValueError(f"no such user: {user_id}")

        user.profile = profile
        user.profile_schema_version = schema_version
        return user.profile_generation

    def profile(self, user_id: _uuid.UUID) -> dict | None:
        user = self._s.get(User, user_id)
        return user.profile if user is not None else None

    def active_configs(self) -> dict[_uuid.UUID, dict]:
        """Every active user's config, for the harvester's query-set fold."""
        rows = self._s.execute(
            select(UserConfig.user_id, UserConfig.config)
            .join(User, User.id == UserConfig.user_id)
            .where(User.active.is_(True), User.status == "active"))
        return dict(rows.all())

    # ── secrets: metadata only, never values ─────────────────────────────────────────────────

    def secret_metadata(self, user_id: _uuid.UUID) -> list[SecretMetadata]:
        """What the UI may show about this user's credentials."""
        rows = self._s.execute(
            select(UserSecret.kind, UserSecret.last_4, UserSecret.validated_at,
                   UserSecret.updated_at)
            .where(UserSecret.user_id == user_id)
            .order_by(UserSecret.kind))
        return [SecretMetadata(*r) for r in rows]

    # ── queue ────────────────────────────────────────────────────────────────────────────────

    def pending_jobs(self, user_id: _uuid.UUID, lane: str | None = None) -> list[QueueJob]:
        """This user's queued work."""
        stmt = select(QueueJob).where(
            QueueJob.user_id == user_id, QueueJob.state.in_(("ready", "claimed")))
        if lane is not None:
            stmt = stmt.where(QueueJob.lane == lane)
        return list(self._s.scalars(stmt.order_by(QueueJob.run_after)))

    # ── model artifacts ──────────────────────────────────────────────────────────────────────

    def prune_model_artifacts(self, user_id: _uuid.UUID, *,
                              keep: int = KEEP_MODEL_VERSIONS) -> list[str]:
        """Keep the current artifact and one previous; return the paths of the rest (G12)."""
        rows = list(self._s.scalars(
            select(UserModel)
            .where(UserModel.user_id == user_id)
            .order_by(UserModel.version.desc())))
        doomed = rows[keep:]
        for row in doomed:
            self._s.delete(row)
        return [row.path for row in doomed]

    # ── the LinkedIn visibility gate ─────────────────────────────────────────────────────────

    def linkedin_visible(self, user_id: _uuid.UUID) -> bool:
        """Whether this user may see `source='linkedin'` rows."""
        status = self._s.scalar(
            select(ProxyCredential.status).where(ProxyCredential.user_id == user_id))
        return status == "healthy"

    def visible_feed(self, user_id: _uuid.UUID, *, limit: int = 50,
                     min_score: float | None = None) -> list[CorpusJob]:
        """This user's feed, with the gate applied."""
        stmt = (
            select(CorpusJob)
            .join(UserJobState, UserJobState.job_id == CorpusJob.id)
            .where(UserJobState.user_id == user_id, UserJobState.notified_at.is_(None)))

        if not self.linkedin_visible(user_id):
            stmt = stmt.where(CorpusJob.source != "linkedin")
        if min_score is not None:
            stmt = stmt.where(UserJobState.score >= min_score)

        return list(self._s.scalars(
            stmt.order_by(UserJobState.score.desc().nullslast(), CorpusJob.first_seen_at.desc())
            .limit(limit)))

    # ── labels ───────────────────────────────────────────────────────────────────────────────

    def upsert_label(self, user_id: _uuid.UUID, job_id: int, *, label: int,
                     source: str = "llm", rationale: str | None = None,
                     weight: float = 1.0, profile_version: str | None = None,
                     profile_generation: int = 0) -> None:
        """Write a training label, replacing any existing one for this `(user, job)`."""
        values = {
            "user_id": user_id, "job_id": job_id, "label": label, "source": source,
            "rationale": rationale, "weight": weight, "profile_version": profile_version,
            "profile_generation": profile_generation,
        }
        self._s.execute(
            pg_insert(UserLabel).values(**values)
            .on_conflict_do_update(
                index_elements=[UserLabel.user_id, UserLabel.job_id],
                set_={k: v for k, v in values.items() if k not in ("user_id", "job_id")}))

    def training_labels(self, user_id: _uuid.UUID, *, profile_generation: int,
                        min_description_chars: int = 100) -> Sequence[UserLabel]:
        """This user's labels for the current profile generation, joined to usable job text."""
        return list(self._s.scalars(
            select(UserLabel)
            .join(CorpusJob, CorpusJob.id == UserLabel.job_id)
            .where(UserLabel.user_id == user_id,
                   UserLabel.profile_generation == profile_generation,
                   func.length(CorpusJob.description) >= min_description_chars)
            .order_by(UserLabel.created_at)))
