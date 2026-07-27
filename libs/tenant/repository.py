"""Per-user repository: job state, applied log, labels, and feedback.

Ports four of the reference's JSONL stores at once, because in the multi-tenant schema they are the
same two tables rather than four files:

| Reference file | Here |
|---|---|
| `applied.jsonl` | `user_job_state.applied_at` / `.cover_built` |
| `labels.jsonl` | `user_labels` with `source='llm'` |
| `feedback.jsonl` | `user_labels` with `source='feedback'`, `weight=5` |
| per-user `seen` | `user_job_state.notified_at` |

**Feedback is not a separate store — it is a label with a different provenance and weight.** The
reference keeps it apart and blends it at train time by duplicating rows five times so the weighting
survives sklearn Pipelines. Recording `source` and `weight` on the row instead makes the blend a
query rather than a data-munging step, and means a change to the weighting policy does not silently
rewrite what historical rows meant.

**Nothing here stores job text.** Every row references `job_id`; the description lives once in the
shared corpus. That is what takes a label row from ~2,937 bytes to ~200 and is the entire storage
argument for the shared design (`PLATFORM_ARCHITECTURE.md` §4). The corpus row is `pin()`ned
whenever a user acts on it, so retention cannot remove text a label or applied-record depends on.
"""
from __future__ import annotations

import uuid as _uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.corpus.repository import CorpusRepository
from libs.db.models import CorpusJob, ProxyCredential, UserJobState, UserLabel

# The reference's SCORER_FEEDBACK_WEIGHT default. A user's explicit decision is worth more than the
# LLM's guess at their taste, and this is the number that says how much more.
FEEDBACK_WEIGHT = 5.0

# The reference maps its two explicit verdicts onto scores at these points, so a feedback row sits
# unambiguously above/below the keep threshold when blended with LLM labels.
APPLIED_SCORE, SKIPPED_SCORE = 90, 10


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
    """All per-user reads and writes. **`user_id` is required on every method.**

    Not optional, not defaulted, not inferred from ambient state. A tenant-scoped query that forgets
    its tenant should fail to construct rather than return another user's rows
    (`TENANT_SAFETY.md` §1.3).
    """

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
        """Store a fit score and which model produced it.

        `model_version` is recorded so a retrain can invalidate stale scores without a full rescore,
        and so "why did this rank differently yesterday" is answerable.
        """
        row = self._state(user_id, job_id)
        row.score = score
        row.scored_at = func.now()
        row.scored_model_version = model_version

    def mark_notified(self, user_id: _uuid.UUID, job_id: int) -> None:
        """Delivery dedup. **Write this in the same transaction that marks the queue job done.**

        The queue gives at-least-once delivery, so this row is what stops a retried job re-sending a
        digest. Committing it separately would leave a window where the send happened and the record
        did not — which is precisely the duplicate the protocol is supposed to make impossible
        (`SCHEDULER_PLAN.md` §4).
        """
        self._state(user_id, job_id).notified_at = func.now()

    # ── applied log ──────────────────────────────────────────────────────────────────────────

    def record_applied(self, user_id: _uuid.UUID, job_id: int, *,
                       cover_built: bool = False) -> bool:
        """Log that this user built a CV for this job. Returns True if it is newly applied.

        Two behaviours carried from the reference:

        - **`applied_at` is set once and never moved.** A later cover build supersedes the row but
          must not rewrite the date the user actually applied.
        - **The corpus row is pinned**, so retention can never remove the JD underneath an applied
          record. This is the one place where a per-user action has to reach into shared state, and
          it is why `pin()` exists.
        """
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

    # ── the LinkedIn visibility gate ─────────────────────────────────────────────────────────

    def linkedin_visible(self, user_id: _uuid.UUID) -> bool:
        """Whether this user may see `source='linkedin'` rows.

        **This is what makes free-riding impossible by construction** (`HARVESTER_PLAN.md` §5.2).
        LinkedIn rows land in the shared corpus so dedup and embed-once keep working, but the tier
        costs real proxy bandwidth paid by whoever fetched it. Without a gate, a user with no
        credential would read results other people paid for.

        One predicate on a column the corpus already records — no per-row provenance, no
        partitioning, no duplicated storage.

        A user without a credential still gets the ~2/3 of sources that cost nothing, which is a
        genuinely useful product on its own and a clear reason to add one.
        """
        status = self._s.scalar(
            select(ProxyCredential.status).where(ProxyCredential.user_id == user_id))
        return status == "healthy"

    def visible_feed(self, user_id: _uuid.UUID, *, limit: int = 50,
                     min_score: float | None = None) -> list[CorpusJob]:
        """This user's feed, with the gate applied.

        Ordering is by score, then recency. The gate is a `WHERE` clause rather than a
        post-filter on purpose: filtering after the `LIMIT` would silently return short pages to
        exactly the users who have no credential.
        """
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
        """Write a training label, replacing any existing one for this `(user, job)`.

        Upsert rather than append, matching the reference's `(user_id, job_id)` keying: a job
        relabelled under a newer rubric must supersede its old label, not sit beside it and let the
        trainer see the same job twice with two verdicts.

        `profile_version` and `profile_generation` are copied onto the row at write time, because a
        user editing their profile changes what "good match" means — labels from the old rubric are
        answers to a different question, and recording the generation is what lets the trainer
        select only the ones that still apply (`PROFILE_CAPTURE_PLAN.md` §6.4).
        """
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
        """An explicit user decision — the flywheel.

        Positive is "worth applying to" (an explicit `/applied`, or a successful CV build, which the
        reference treats as the same strong signal). Negative is an explicit `/skip`.

        **Passive non-application is never recorded.** Only explicit decisions count, which is what
        keeps the signal clean — a job the user simply did not get to is not a rejection, and
        training on that assumption would teach the model the opposite of what they meant.
        """
        self.upsert_label(
            user_id, job_id,
            label=APPLIED_SCORE if applied else SKIPPED_SCORE,
            source="feedback", weight=FEEDBACK_WEIGHT,
            profile_version=profile_version, profile_generation=profile_generation)

    def training_labels(self, user_id: _uuid.UUID, *, profile_generation: int,
                        min_description_chars: int = 100) -> Sequence[UserLabel]:
        """This user's labels for the current profile generation, joined to usable job text.

        Two filters, both carried from the reference's `load_clean()`:

        - **Only the current `profile_generation`.** Labels from an older rubric answer a different
          question; mixing them trains on a blend of two definitions of "good".
        - **Drop thin descriptions.** Under ~100 characters means the fetch failed, and the label
          describes a page that was never really read. Training on those teaches noise.
        """
        return list(self._s.scalars(
            select(UserLabel)
            .join(CorpusJob, CorpusJob.id == UserLabel.job_id)
            .where(UserLabel.user_id == user_id,
                   UserLabel.profile_generation == profile_generation,
                   func.length(CorpusJob.description) >= min_description_chars)
            .order_by(UserLabel.created_at)))
