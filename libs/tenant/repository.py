"""Per-user repository: job state, applied log, labels, and feedback."""
from __future__ import annotations

import uuid as _uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.contracts.config import CONFIG_SCHEMA_VERSION
from libs.contracts.profile import PROFILE_SCHEMA_VERSION, ProfileSchema
from libs.corpus.repository import CorpusRepository
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
from libs.db.models.user import DORMANT_AFTER_DAYS
from libs.feedback.actions import FEEDBACK_WEIGHT
from libs.scoring.facts import render_facts
from libs.scoring.rubric import profile_version, render_rubric, rubric_relevant_digest

# The reference maps its two verdicts onto these scores, so feedback blends with LLM labels.
APPLIED_SCORE, SKIPPED_SCORE = 90, 10

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
        self._corpus = CorpusRepository(session)

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

    # ── applied log ──────────────────────────────────────────────────────────────────────────

    def record_applied(self, user_id: _uuid.UUID, job_id: int, *,
                       cover_built: bool = False) -> bool:
        """Log that this user built a CV for this job."""
        row = self._state(user_id, job_id)
        is_new = row.applied_at is None
        if is_new:
            row.applied_at = func.now()
        if cover_built:
            row.cover_built = True
        self._corpus.pin(job_id)
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
        """Store a confirmed profile, render its rubric and facts block, bump the generation."""
        if not profile.get("confirmed"):
            raise ValueError("refusing to store an unconfirmed profile — see PROFILE_CAPTURE_PLAN §5")
        user = self._s.get(User, user_id)
        if user is None:
            raise ValueError(f"no such user: {user_id}")

        validated = ProfileSchema.model_validate(profile)
        # Compared before anything is written.
        previous = user.profile
        try:
            unchanged = (previous is not None
                         and rubric_relevant_digest(ProfileSchema.model_validate(previous))
                         == rubric_relevant_digest(validated))
        except ValidationError:
            unchanged = False

        rubric = render_rubric(validated)
        user.profile = profile
        user.profile_schema_version = schema_version
        user.rubric_md = rubric
        # Rendered in the same write as the rubric.
        user.facts_md = render_facts(validated)
        user.profile_version = profile_version(rubric)
        if not unchanged:
            user.profile_generation += 1
        return user.profile_generation

    def refresh_facts(self, user_id: _uuid.UUID) -> bool:
        """Re-render the facts block from the stored profile."""
        user = self._s.get(User, user_id)
        if user is None or not user.profile:
            return False
        user.facts_md = render_facts(ProfileSchema.model_validate(user.profile))
        return True

    def profile(self, user_id: _uuid.UUID) -> dict | None:
        user = self._s.get(User, user_id)
        return user.profile if user is not None else None

    def touch_activity(self, user_id: _uuid.UUID) -> None:
        """Record engagement."""
        self._s.execute(
            update(User).where(User.id == user_id).values(last_active_at=func.now()))

    def mark_dormant(self, *, after_days: int = DORMANT_AFTER_DAYS) -> list[_uuid.UUID]:
        """Pause accounts with no engagement for `after_days`."""
        cutoff = datetime.now(UTC) - timedelta(days=after_days)
        rows = self._s.scalars(
            select(User.id).where(
                User.status == "active",
                func.coalesce(User.last_active_at, User.created_at) < cutoff))
        paused = list(rows)
        if paused:
            self._s.execute(
                update(User).where(User.id.in_(paused))
                .values(status="dormant", active=False))
        return paused

    def reactivate(self, user_id: _uuid.UUID) -> bool:
        """Bring a dormant account back."""
        user = self._s.get(User, user_id)
        if user is None or user.status != "dormant":
            return False
        user.status = "active"
        user.active = True
        user.last_active_at = func.now()
        return True

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

    # ── labels and feedback ──────────────────────────────────────────────────────────────────

    def upsert_label(self, user_id: _uuid.UUID, job_id: int, *, label: int,
                     source: str = "llm", rationale: str | None = None,
                     weight: float | None = None, profile_version: str | None = None,
                     profile_generation: int = 0) -> None:
        """Write a training label, replacing any existing one for this `(user, job)`."""
        if weight is None:
            weight = FEEDBACK_WEIGHT if source == "feedback" else 1.0
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
        self._corpus.pin(job_id)

    def record_feedback(self, user_id: _uuid.UUID, job_id: int, *, applied: bool,
                        profile_version: str | None = None,
                        profile_generation: int = 0) -> None:
        """An explicit user decision — the flywheel."""
        self.upsert_label(
            user_id, job_id,
            label=APPLIED_SCORE if applied else SKIPPED_SCORE,
            source="feedback", weight=FEEDBACK_WEIGHT,
            profile_version=profile_version, profile_generation=profile_generation)

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
