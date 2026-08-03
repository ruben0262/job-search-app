"""The shared job corpus — the one table that is not per-user, and must never become per-user.

Public job postings only. No column here may ever hold anything belonging to a user: that single
rule is what makes account deletion one cascading statement (PLATFORM_ARCHITECTURE.md §8) and what
keeps the storage projection true (0.87 GB steady state, against 44 GB for per-user copies at 50
users, §4). It is worth defending in review, because the pressure to denormalise "just one score"
onto a corpus row will come.

**Retention is tiered, and that is what makes the steady state steady.** A row keeps its description
and vector for ``CORPUS_HOT_DAYS`` (90). After that, if no user has acted on it, both are dropped and
the ~258-byte dedup key is kept forever. Rows any user applied to, built a CV for, or labelled are
``pinned`` and never trimmed.

**The vector is keyed on a content hash, not on the row id.** ``refresh.py`` in the reference
instance re-fetches and mutates descriptions on live postings, so a vector keyed by job id silently
goes stale against text it no longer represents — and a stale vector does not error, it just scores
wrong. ``text_sha256`` is compared before trusting ``embedding``; a mismatch means re-embed.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base, timestamptz


class CorpusJob(Base):
    __tablename__ = "corpus"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # --- dedup: the load-bearing part (port of pipeline/lib/store.py) ---
    # Three keys, narrowing. `url` is what a source gave us; `canonical_url` folds tracking params
    # and host variants; `content_key` catches the same posting re-listed under a different URL, and
    # is location-aware because the same title+company in two cities is two jobs, not one.
    # These semantics are tuned and quietly load-bearing — T2 pins them before the port begins.
    #
    # The shape mirrors the reference's `all_links` deliberately, and two details matter:
    #
    # 1. **`url` is the identity** (it is the PRIMARY KEY there). `canonical_url` and `content_key`
    #    are *indexed, not unique*. Dedup is a query performed BEFORE insert, not a constraint.
    #    Making canonical unique looks tighter but changes behaviour: the reference tolerates two
    #    rows sharing a canonical, and a constraint would turn that tolerated case into a crash in
    #    the middle of a harvest cycle.
    # 2. **Both derived keys are nullable**, because `canonical_url()` and `content_key()` return
    #    None for input they cannot use — a relative URL, a posting with no company. That is the
    #    "no opinion, fail open" contract from libs/corpus/keys.py. NOT NULL here would make such a
    #    job impossible to store at all, silently dropping real postings rather than admitting them
    #    undeduplicated, which is the wrong direction to fail.
    url: Mapped[str] = mapped_column(Text, unique=True)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    # Text, not String(64). `content_key` is a READABLE COMPOSITE - "company|title|location" - not
    # a hash, and its length is a function of a job title somebody else wrote. A German posting
    # ("osnatruck nutzfahrzeugservice|mitarbeiter buchhaltung in voll oder teilzeit|osnabruck")
    # blew the bound on the first real harvest cycle. Same mistake as embedding_version: bounding
    # something whose length is not ours to bound.
    content_key: Mapped[str | None] = mapped_column(Text)

    # The resolved external "Apply" target, and its canonical form. This is a FOURTH dedup layer and
    # it is the one the whole canonical scheme was built for: the same job arrives as
    # linkedin.com/jobs/view/… from the LinkedIn scrape and boards.greenhouse.io/… from the direct
    # ATS poll, and nothing about those two URLs is comparable. Resolving LinkedIn's Apply button to
    # its external target makes them comparable — the reference indexes exactly this as
    # `processed.app_canonical` and matches on `canonical = ? OR app_canonical = ?`.
    #
    # Nullable because most rows never need it: an ATS row IS its own apply target, and resolution
    # only happens for aggregator sources during description fetch.
    application_url: Mapped[str | None] = mapped_column(Text)
    application_canonical_url: Mapped[str | None] = mapped_column(Text)

    # `source` is the whole of the LinkedIn visibility gate. Rows scraped on one user's proxy
    # credential still land here so dedup and embed-once keep working, but a user sees them only
    # while they hold a healthy credential — one filter predicate, no data partitioning, no
    # per-row provenance (PLATFORM_ARCHITECTURE.md §6).
    source: Mapped[str] = mapped_column(String(48))

    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    is_remote: Mapped[bool | None] = mapped_column(Boolean)

    # Nullable because a row exists from the moment it is discovered, and the description is fetched
    # in a later step — and because trimming sets it back to NULL after CORPUS_HOT_DAYS.
    description: Mapped[str | None] = mapped_column(Text)
    description_fetched_at: Mapped[timestamptz | None]

    # --- embedding: computed once, for everyone, ever ---
    # bytea rather than pgvector: nothing does similarity *search* yet. The vector is read whole and
    # multiplied by a user's classifier, so a blob is sufficient and avoids an extension dependency.
    # Revisit if search is ever wanted.
    embedding: Mapped[bytes | None]
    text_sha256: Mapped[str | None] = mapped_column(String(64))
    # The embedder's version contract, stored per row. A scorer whose artifact was trained against a
    # different (model, dim, version) must fail closed rather than score across vector spaces — the
    # discipline the embedder already enforces at its own boundary (PLATFORM_ARCHITECTURE.md §5).
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    # Text, not String(32). The embedder's version is a COMPOSITE of everything that defines the
    # vector space - "BAAI/bge-base-en-v1.5|dim=768|norm=1" is already 36 characters, so the
    # original bound would have failed on the very first real embed. Its length is a function of the
    # model name, which is not ours to bound.
    embedding_version: Mapped[str | None] = mapped_column(Text)
    # When the vector was written. **The middle of the pipeline had no clock.** A row recorded when
    # its text arrived and then jumped to having an embedding, so "described → embedded" and
    # "embedded → scored" were unmeasurable and both hid inside one found → scored number that could
    # not say which of them was slow (ROADMAP 6p). Cleared by trimming with the vector it dates, so
    # it never outlives the thing it describes.
    embedded_at: Mapped[timestamptz | None]

    # ── what the feed card shows about a posting ─────────────────────────────────────────────
    # The reference's card answers "how contested, how fresh, one-click or not, and where" before
    # anybody opens the link (`watcher/feed.py`). All nullable and opportunistic: a source that does
    # not report a field is not a reason to withhold the job, and the card omits the element.
    applicants: Mapped[int | None] = mapped_column(Integer)
    easy_apply: Mapped[bool | None] = mapped_column(Boolean)
    reposted: Mapped[bool | None] = mapped_column(Boolean)
    # 'remote' | 'hybrid' | 'onsite'. NULL means unknown, and unknown means keep — the fail-open
    # rule `libs/corpus/workfilter.py` applies.
    workplace: Mapped[str | None] = mapped_column(String(8))
    # When the posting said "no longer accepting applications". **Not a deletion**: the row stays,
    # the description stays, and a CV already built against it keeps its evidence — what changes is
    # that it stops being delivered. A closed listing in a feed is worse than no listing, because
    # the user spends attention and sometimes a build before finding out.
    closed_at: Mapped[timestamptz | None]

    posted_at: Mapped[timestamptz | None]
    first_seen_at: Mapped[timestamptz] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[timestamptz] = mapped_column(server_default=func.now())

    # Set when any user acts on the row (applied, built a CV, labelled). Pinned rows survive
    # trimming forever — losing the text under a label would leave the label meaningless.
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    trimmed_at: Mapped[timestamptz | None]

    schema_version: Mapped[int] = mapped_column(SmallInteger, default=1)

    __table_args__ = (
        # Dedup lookup is the hot path on every harvest cycle, for every candidate row: each
        # candidate is checked url → canonical → content_key, so all three need an index and only
        # `url` needs to be unique.
        Index("ix_corpus_canonical_url", "canonical_url"),
        Index("ix_corpus_content_key", "content_key"),
        # The apply-URL layer is queried against BOTH columns (a candidate's own canonical may match
        # another row's apply target and vice versa), so both need to be indexed.
        Index("ix_corpus_application_canonical_url", "application_canonical_url"),
        # The per-user match pass: "recent rows from sources this user can see".
        Index("ix_corpus_source_first_seen", "source", "first_seen_at"),
        # Finds rows still awaiting an embedding, without scanning the whole corpus.
        # Every read of `closed_at` asks for the rows where it is NULL — nearly all of them early on
        # and a shrinking share later — so the partial index covers the open ones, not the closed.
        Index("ix_corpus_open", "id", postgresql_where=text("closed_at IS NULL")),
        Index("ix_corpus_unembedded", "id",
              postgresql_where=text("embedding IS NULL AND description IS NOT NULL")),
        # Drives the retention sweep.
        Index("ix_corpus_trim_candidates", "first_seen_at",
              postgresql_where=text("pinned = false AND trimmed_at IS NULL")),
        CheckConstraint(
            "(embedding IS NULL) OR (text_sha256 IS NOT NULL AND embedding_dim IS NOT NULL)",
            name="embedding_has_provenance"),
        CheckConstraint("workplace IS NULL OR workplace IN ('remote', 'hybrid', 'onsite')",
                        name="corpus_workplace_valid"),
    )
