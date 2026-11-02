# libs/db — the schema, in one place

MongoDB Atlas is the only datastore (ROADMAP Phase 9, replacing Postgres on 2026-10-30): accounts,
their documents and credentials, the shared job corpus, and the work queue, in one database.

## Current state

✅ **Built.** One dataclass per collection in `models/`, registered in `models.MODELS`, applied by
`ensure_schema()`. 25 collections. The switched-off model's Postgres schema is kept under the git tag
`model-on-postgres`.

## How it works

- **`database()`** — one client per process, from `MONGODB_URI` and `MONGODB_DB` (default
  `mahoraga`). UUIDs in the standard form, datetimes timezone-aware UTC. `now()` is the time to the
  millisecond, which is all MongoDB keeps.
- **A collection is a dataclass** (`Document`): its fields and defaults, its `INDEXES`, the values a
  field may hold (`CHOICES`, enforced by the database on every write), who owns it (`OWNER`), whether
  its ids are integers from a counter (`SEQUENCE`), and whether the nightly backup keeps it
  (`BACKED_UP`). A field the class does not declare fails on read rather than vanishing.
- **`ensure_schema()`** makes the database match: collections, rules, indexes. Idempotent, safe for
  several services starting at once, run by every service as it starts. Only the deploy's
  `python -m libs.db --prune` drops an index nothing declares, so older code never undoes newer.
- **`delete_account()`** removes everything an account owns — every collection with an `OWNER`, plus
  the PDFs its builds made — which is what `ON DELETE CASCADE` used to do.
- **`lease()`** — a named lease for work that must not run twice at once (the harvest cycle).
- **`insert`, `get`, `find_one`, `find`** — the thin layer between dataclasses and documents.

Each write is its own, and atomic for its one document; nothing uses a multi-document transaction, so
a standalone `mongod` serves for development and tests.

## Rules

- **Add a collection by adding a dataclass to `models/` and to `MODELS`.** `tests/test_schema.py`
  fails on a document class that is not registered.
- **Every field needs a writer** — `tests/test_columns_have_producers.py` fails the build on a field
  nothing writes.
- **Never point tests at Atlas.** `tests/conftest.py` refuses it.
