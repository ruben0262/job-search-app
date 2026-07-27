"""Shared ATS harvester state: company tokens and conditional-poll ETags.

Shared, never per-user — these describe public job boards. A token harvested because one user's
search surfaced it makes polling cheaper for everyone, the same dividend the shared corpus pays.

**The ETag half is the one place in this port where the new implementation is a fix rather than a
translation.** The reference keeps `ats_etags.json` behind a file spinlock and rewrites the whole
map for an ATS on every save. Two independent ways that loses data:

1. `_acquire_lock()` returns `False` on timeout and **the caller proceeds anyway** — a contended
   write silently discards another writer's keys.
2. Even holding the lock, the write is a whole-map replace, so two ATS gathers finishing together
   clobber each other wholesale.

Row-level upsert on `(ats, token)` removes both by construction: concurrent writers touch different
rows and never serialise on a file. Nothing here needs a lock.

Why it is worth the care: the ETag cache is what makes ATS polling near-free — a board answering
`304 Not Modified` costs one request and no parsing (~40 s cold, near-free warm). Breaking it means
re-downloading every board every cycle, which is slow *and* a much larger footprint against sources
already being polled on sufferance.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.db.models import AtsEtag, AtsToken


class AtsRepository:
    """Company tokens and ETags for the direct-ATS tier. Shared; takes no `user_id`."""

    def __init__(self, session: Session):
        self._s = session

    # ── tokens ───────────────────────────────────────────────────────────────────────────────

    def live_tokens(self, ats: str) -> list[str]:
        """Every token still worth polling for this ATS, oldest first.

        Excludes tokens marked dead. They are retained rather than deleted so a stale apply URL
        cannot cause the same 404 board to be re-harvested and re-polled every cycle.
        """
        return list(self._s.scalars(
            select(AtsToken.token)
            .where(AtsToken.ats == ats, AtsToken.dead_at.is_(None))
            .order_by(AtsToken.first_seen_at)))

    def add_tokens(self, pairs: Iterable[tuple[str, str]]) -> int:
        """Merge `(ats, token)` pairs. Returns how many were newly added.

        Tokens are lowercased and stripped before storage because every ATS API is case-insensitive
        on them — normalising once at the boundary is what keeps the harvested list genuinely
        deduplicated rather than holding three spellings of the same company.

        `ON CONFLICT DO NOTHING` means re-harvesting an existing token is free and never disturbs
        its `first_seen_at`, and the count returned is a true count of *new* companies.
        """
        rows = []
        seen: set[tuple[str, str]] = set()
        for ats, token in pairs:
            ats_n = (ats or "").strip().lower()
            token_n = (token or "").strip().lower()
            if not ats_n or not token_n or (ats_n, token_n) in seen:
                continue
            seen.add((ats_n, token_n))
            rows.append({"ats": ats_n, "token": token_n})
        if not rows:
            return 0

        result = self._s.execute(
            pg_insert(AtsToken).values(rows)
            .on_conflict_do_nothing(index_elements=[AtsToken.ats, AtsToken.token])
            .returning(AtsToken.token))
        return len(result.fetchall())

    def mark_dead(self, ats: str, token: str) -> None:
        """A board that 404s. Kept, not deleted — see `live_tokens`."""
        self._s.execute(
            update(AtsToken)
            .where(AtsToken.ats == ats, AtsToken.token == token, AtsToken.dead_at.is_(None))
            .values(dead_at=func.now()))

    def revive(self, ats: str, token: str) -> None:
        """A board that answered again after being marked dead."""
        self._s.execute(
            update(AtsToken)
            .where(AtsToken.ats == ats, AtsToken.token == token)
            .values(dead_at=None))

    # ── etags ────────────────────────────────────────────────────────────────────────────────

    def etags(self, ats: str) -> dict[str, str]:
        """Every known ETag for this ATS as `{token: etag}` — the shape the poll loop expects."""
        return dict(
            self._s.execute(select(AtsEtag.token, AtsEtag.etag).where(AtsEtag.ats == ats)).all())

    def set_etag(self, ats: str, token: str, etag: str) -> None:
        """Record a validator. Called on `200` (new ETag) and on `304` (the same one, still valid).

        A `304` **keeps** the existing ETag rather than clearing it — dropping it there would make
        the next poll unconditional and throw away the entire saving this cache exists for.

        One row, upserted. No lock, and no read-modify-write of anything another writer also holds.
        """
        if not etag:
            return
        self._s.execute(
            pg_insert(AtsEtag)
            .values(ats=ats, token=token, etag=etag)
            .on_conflict_do_update(
                index_elements=[AtsEtag.ats, AtsEtag.token],
                set_={"etag": etag, "updated_at": func.now()}))

    def forget_etag(self, ats: str, token: str) -> None:
        """Drop a validator for a dead or empty board.

        So that a board which comes back is fetched in full, rather than being compared against a
        validator from before it disappeared — which could answer `304` against content that is no
        longer what we last parsed.
        """
        self._s.execute(delete(AtsEtag).where(AtsEtag.ats == ats, AtsEtag.token == token))

    def record_poll(self, ats: str, token: str, *, etag: str | None, alive: bool) -> None:
        """Apply one board's poll outcome. The reference's `new_etags` bookkeeping, per row.

        Collapses the three cases the poll loop distinguishes:

        - **ETag returned** (200 or 304) → store it, and revive the token if it had been dead.
        - **No ETag, board alive** → nothing to store; leave any existing validator alone.
        - **Board dead or empty** → forget the ETag and mark the token dead.
        """
        if not alive:
            self.forget_etag(ats, token)
            self.mark_dead(ats, token)
            return
        if etag:
            self.set_etag(ats, token, etag)
