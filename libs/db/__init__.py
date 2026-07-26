"""SQLAlchemy models and Alembic migrations — the schema lives here, in one place.

Postgres is the only datastore (decision D1): one database for the shared corpus, per-user tail,
credentials, and the four-lane job queue. That choice is what makes account deletion correct by
construction — every per-user row carries ``user_id UUID REFERENCES users(id) ON DELETE CASCADE``
(PLATFORM_ARCHITECTURE.md §8) — instead of correct by remembering to write cleanup code.

Two rules that are cheap now and expensive later (GAPS_REVIEW G5, G7):

- **Alembic from the first table.** No hand-applied DDL, ever.
- **``TIMESTAMPTZ`` everywhere** and ``users.timezone`` as an IANA name, so per-day counters can
  reset at the user's local midnight rather than the server's.

**Migrations run as their own one-shot step** (``alembic upgrade head``), never from a service
entrypoint — two services racing to migrate on restart is a real outage (CODE_STRUCTURE.md §5).
Schema changes are additive-first (add column → deploy code → backfill → drop later) so old and
new code can both run against the same database during a rollout.
"""
