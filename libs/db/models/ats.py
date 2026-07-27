"""Shared harvester state for the direct-ATS tier: company tokens, and conditional-poll ETags.

Both are **shared, not per-user** — they describe public job boards, not people. A token harvested
because one user's search surfaced it makes polling cheaper for everyone, which is the same
dividend the shared corpus pays.

These two are the easiest stores in the whole port to underestimate (`PLATFORM_ARCHITECTURE.md` §4):

- **`ats_tokens`** is ~900 company tokens accumulated for free from hiring.cafe hits and from apply
  URLs on jobs found anywhere else. Losing it means re-discovering every company.
- **`ats_etags`** is what makes ATS polling near-free — ~40 s cold, near-free warm, because a board
  that answers `304 Not Modified` costs one request and no parsing. Breaking it means
  re-downloading every board every cycle, which is both slow and a much larger footprint against
  sources that are already being polled on sufferance.

**Why the ETag store is a table and not a JSON file with a lock.** The reference keeps
`ats_etags.json` guarded by a file spinlock and rewrites the *whole map for an ATS* on every save.
Two things make that unsurvivable here:

1. `_acquire_lock()` returns False on timeout and the caller **proceeds anyway**, so a contended
   write silently loses another writer's keys.
2. Even when the lock is held, the write is a whole-map replace. Two ATS gathers finishing together
   can clobber each other's tokens wholesale.

Row-level upsert on `(ats, token)` removes both by construction: concurrent writers touch different
rows and never serialise on a file at all. This is the one store where the port is a *fix* rather
than a translation.
"""
from __future__ import annotations

from sqlalchemy import Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class AtsToken(Base):
    """A company's board token on one ATS. Harvested, never hand-maintained.

    `(ats, token)` is the identity. Tokens are lowercased before storage because every ATS API is
    case-insensitive on them (Workday's site segment was verified too) — normalising once at the
    boundary is what keeps the harvested list actually deduplicated.
    """

    __tablename__ = "ats_tokens"

    ats: Mapped[str] = mapped_column(String(32), primary_key=True)
    token: Mapped[str] = mapped_column(String(200), primary_key=True)
    first_seen_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    # Set when a board 404s. Kept rather than deleted so a dead token is not re-harvested and
    # re-polled on the next cycle that finds the same stale apply URL.
    dead_at: Mapped[timestamptz | None]

    # The poll loop's only read: "every live token for this ATS". Partial, because dead tokens are
    # retained for the reason above but must never be polled.
    __table_args__ = (
        Index("ix_ats_tokens_live", "ats", postgresql_where=text("dead_at IS NULL")),
    )


class AtsEtag(Base):
    """The last ETag seen for one company's board. Enables `If-None-Match` conditional polling.

    Row per `(ats, token)`, so the whole-map rewrite the reference performs becomes an upsert of a
    single row. Two rules carried over exactly, because both are load-bearing:

    - A `304` response **keeps** the existing ETag (the board is unchanged, the validator still
      valid). Dropping it there would make the next poll unconditional and lose the saving.
    - A dead or empty board **forgets** its ETag, so a board that comes back is fetched in full
      rather than being compared against a validator from before it disappeared.
    """

    __tablename__ = "ats_etags"

    ats: Mapped[str] = mapped_column(String(32), primary_key=True)
    token: Mapped[str] = mapped_column(String(200), primary_key=True)
    etag: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[timestamptz] = mapped_column(server_default=func.now(), onupdate=func.now())
