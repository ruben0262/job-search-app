"""The shared job corpus — the one table that is not per-user, and must never become per-user."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class CorpusJob(Base):
    __tablename__ = "corpus"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Dedup, the load-bearing part: three keys, narrowing (port of pipeline/lib/store.py).
    url: Mapped[str] = mapped_column(Text, unique=True)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    # Text, not String(64): a readable composite whose length a job title decides, not us.
    content_key: Mapped[str | None] = mapped_column(Text)

    # The resolved external "Apply" target, and its canonical form.
    application_url: Mapped[str | None] = mapped_column(Text)
    application_canonical_url: Mapped[str | None] = mapped_column(Text)

    # `source` is the whole of the LinkedIn visibility gate.
    source: Mapped[str] = mapped_column(String(48))

    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    # The location string, read once, as a fact.
    country: Mapped[str | None] = mapped_column(String(2))
    is_remote: Mapped[bool | None] = mapped_column(Boolean)
    # The description, read once: a property of the posting rather than of whoever is reading it.
    language: Mapped[str | None] = mapped_column(String(8))

    # Nullable: a row exists from discovery, the description arrives later, and trimming clears it.
    description: Mapped[str | None] = mapped_column(Text)
    description_fetched_at: Mapped[timestamptz | None]

    # Embedding, computed once for everyone. bytea rather than pgvector: nothing searches yet.
    embedding: Mapped[bytes | None]
    text_sha256: Mapped[str | None] = mapped_column(String(64))
    # The embedder's version contract, stored per row.
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    # Text, not String(32).
    embedding_version: Mapped[str | None] = mapped_column(Text)
    # When the vector was written.
    embedded_at: Mapped[timestamptz | None]

    # How this row got here, so the question does not have to be reconstructed from logs again.
    #
    # `discovered_via` is the search term that returned the link — not the posting's own title, and
    # on LinkedIn frequently nothing like it, because its matching is looser than its query. That
    # gap is what made "why is this in my corpus" unanswerable.
    discovered_via: Mapped[str | None] = mapped_column(Text)
    # The account whose harvest found it, for the per-user tiers. NULL for the shared cycle, where
    # one query is folded across every account that asked for it and no single owner exists.
    discovered_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)

    # Consecutive failed attempts to read this posting's text. Two and it is abandoned: never
    # described, embedded or scored again. The describer had no memory before this, so a page that
    # refused to answer was asked again every fifteen minutes for as long as it stayed in window.
    describe_failures: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")

    # What the feed card shows: how contested, how fresh, one-click or not, and where.
    applicants: Mapped[int | None] = mapped_column(Integer)
    # The number alone means the wrong thing.
    applicants_caption: Mapped[str | None] = mapped_column(Text)
    easy_apply: Mapped[bool | None] = mapped_column(Boolean)
    reposted: Mapped[bool | None] = mapped_column(Boolean)
    # 'remote' | 'hybrid' | 'onsite'.
    workplace: Mapped[str | None] = mapped_column(String(8))
    # When the posting said "no longer accepting applications".
    closed_at: Mapped[timestamptz | None]

    posted_at: Mapped[timestamptz | None]
    # When the request that found this posting produced it — the adapter's own clock.
    fetched_at: Mapped[timestamptz | None]
    # `clock_timestamp()`, not `now()`.
    first_seen_at: Mapped[timestamptz] = mapped_column(server_default=func.clock_timestamp())
    last_seen_at: Mapped[timestamptz] = mapped_column(
        server_default=func.clock_timestamp())

    # `pinned` and `trimmed_at` were dropped 2026-08-11 with the mechanism that used them. Retention
    # now archives the whole row and deletes it (`libs.corpus.archive`), and it asks `user_labels`
    # and `user_job_state` directly whether a row may leave — a flag two of the three label writers
    # never set was the second source of truth, not the answer.

    schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)

    __table_args__ = (
        # Dedup checks url → canonical → content_key on every candidate, so all three need an index.
        Index("ix_corpus_canonical_url", "canonical_url"),
        Index("ix_corpus_content_key", "content_key"),
        # The apply-URL layer is queried against both columns, so both need to be indexed.
        Index("ix_corpus_application_canonical_url", "application_canonical_url"),
        # The per-user match pass: "recent rows from sources this user can see".
        Index("ix_corpus_source_first_seen", "source", "first_seen_at"),
        # Partial, because NULL is never selected on.
        Index("ix_corpus_country", "country", postgresql_where=text("country IS NOT NULL")),
        Index("ix_corpus_language", "language", postgresql_where=text("language IS NOT NULL")),
        # What the score lane reads, in the order it reads it.
        Index("ix_corpus_scorable", text("first_seen_at DESC"),
              postgresql_where=text("closed_at IS NULL AND embedding IS NOT NULL")),
        # Finds rows still awaiting an embedding, without scanning the whole corpus.
        Index("ix_corpus_open", "id", postgresql_where=text("closed_at IS NULL")),
        # Created by 20260808_1930 and missing here, so every autogenerate proposed dropping it.
        Index("ix_corpus_describe_failures", "describe_failures",
              postgresql_where=text("describe_failures > 0")),
        Index("ix_corpus_unembedded", "id",
              postgresql_where=text("embedding IS NULL AND description IS NOT NULL")),
        # Drives retention: the archive pass reads oldest-first, so it needs `first_seen_at` ordered
        # without a partial predicate — what may leave is now decided by a join, not by a flag.
        Index("ix_corpus_first_seen", "first_seen_at"),
        CheckConstraint(
            "(embedding IS NULL) OR (text_sha256 IS NOT NULL AND embedding_dim IS NOT NULL)",
            name="embedding_has_provenance"),
        CheckConstraint("workplace IS NULL OR workplace IN ('remote', 'hybrid', 'onsite')",
                        name="corpus_workplace_valid"),
    )
