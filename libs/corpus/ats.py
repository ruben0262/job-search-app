"""Shared ATS harvester state: company tokens and conditional-poll ETags."""
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
        """Every token still worth polling for this ATS, oldest first."""
        return list(self._s.scalars(
            select(AtsToken.token)
            .where(AtsToken.ats == ats, AtsToken.dead_at.is_(None))
            .order_by(AtsToken.first_seen_at)))

    def add_tokens(self, pairs: Iterable[tuple[str, str]]) -> int:
        """Merge `(ats, token)` pairs."""
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
        """Record a validator."""
        if not etag:
            return
        self._s.execute(
            pg_insert(AtsEtag)
            .values(ats=ats, token=token, etag=etag)
            .on_conflict_do_update(
                index_elements=[AtsEtag.ats, AtsEtag.token],
                set_={"etag": etag, "updated_at": func.now()}))

    def forget_etag(self, ats: str, token: str) -> None:
        """Drop a validator for a dead or empty board."""
        self._s.execute(delete(AtsEtag).where(AtsEtag.ats == ats, AtsEtag.token == token))

    def record_poll(self, ats: str, token: str, *, etag: str | None, alive: bool) -> None:
        """Apply one board's poll outcome."""
        if not alive:
            self.forget_etag(ats, token)
            self.mark_dead(ats, token)
            return
        if etag:
            self.set_etag(ats, token, etag)
