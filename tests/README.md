# tests

Behaviour tests for the whole platform (CLAUDE.md §2). Most need a MongoDB to run against; without
one they skip, and the suite still prints green — **read the skip count, not the colour** (§7).

## Running them

Against a local `mongod`, **never Atlas**: the tests empty every collection they touch, and
`conftest.py` refuses a `MONGODB_URI` that names an Atlas cluster. Each run works in its own
`test_<pid>` database and drops it at the end.

A one-node replica set, because the platform uses transactions and those need one:

```bash
# MongoDB Community Server 8.0 (the version Atlas runs), unpacked anywhere.
mkdir -p /tmp/mongo-tests
mongod --dbpath /tmp/mongo-tests --bind_ip 127.0.0.1 --port 27018 --replSet rs0 --fork \
       --logpath /tmp/mongo-tests/mongod.log
mongosh --port 27018 --eval 'rs.initiate({_id: "rs0", members: [{_id: 0, host: "127.0.0.1:27018"}]})'

export MONGODB_URI="mongodb://127.0.0.1:27018/?replicaSet=rs0"
pytest tests/ -q -rs
```

`CHROME_BIN` (a Chromium binary) runs the browser tests — the PDF renderer and the end-to-end walk
from sign-up to a downloaded CV — which also need a fresh `npm --prefix web run build`.

## What skips, and why

- `test_copy_accounts.py` — the full one-time copy needs a Postgres at the final schema; see its
  docstring. Its conversion rules run without one.
- `test_dedup_parity.py` — compares against the reference instance's checkout, when one is present.
- Any test needing `MONGODB_URI` or `CHROME_BIN`, when they are not set.

## Fixtures

`conftest.py` gives `db` (the test database, emptied after each test, schema applied) and `user`
(one active account). Use them rather than opening a client: everything the platform does goes
through `libs.db.database()`, which reads the same `MONGODB_DB` the fixture sets.
