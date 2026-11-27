#!/usr/bin/env bash
# The gates of CLAUDE.md §7, in their order, as one command. `.github/workflows/ci.yml` runs this same file,
# so there is one list of gates and not two to drift apart (the workflow's own copy still asked for Postgres
# and five services a month after they had gone).
#
#   scripts/gates.sh                  before a commit: every gate, and the tests the change can reach
#   scripts/gates.sh --all            before a push, or after a wide change: every gate and every test
#   scripts/gates.sh --since REV      the tests reached by everything since REV (say origin/main)
#
# Anything after those is handed to pytest (`scripts/gates.sh --all -x`).
#
# Needs a local mongod and a Chromium. Never Atlas: the tests empty every collection they touch.
set -euo pipefail
cd "$(dirname "$0")/.."

tests=(--affected)
case "${1:-}" in
  --all) tests=(); shift ;;
  --since) tests=("--affected=${2:?--since needs a commit}"); shift 2 ;;
esac

# By name, never `. certificates/dev.env`: the rest of that file switches LinkedIn off, and 24 tests fail for it.
export MONGODB_URI="${MONGODB_URI:-mongodb://127.0.0.1:27017}"
case "$MONGODB_URI" in
  *.mongodb.net*) echo "gates: MONGODB_URI names Atlas, and the tests delete what they touch" >&2; exit 2 ;;
esac
if [ -z "${CHROME_BIN:-}" ] && [ -f certificates/dev.env ]; then
  CHROME_BIN="$(sed -n 's/^CHROME_BIN=//p' certificates/dev.env)"
fi
export CHROME_BIN="${CHROME_BIN:-}"
# Without it every browser test skips and the run still prints green: refused here, not read off a skip count.
[ -x "$CHROME_BIN" ] || { echo "gates: CHROME_BIN is not a Chromium (tests/README.md)" >&2; exit 2; }
[ -d .venv/bin ] && PATH="$PWD/.venv/bin:$PATH"

gate() {  # gate "what it is" command...
  local name="$1" started=$SECONDS
  shift
  echo "── $name"
  "$@" || { echo "gates: FAILED at: $name" >&2; exit 1; }
  echo "   ok, $((SECONDS - started)) s"
}

schema() {  # the schema, on an empty database of its own, dropped after
  MONGODB_DB="gate_$$" python -m libs.db --prune >/dev/null
  MONGODB_DB="gate_$$" python -c "from libs.db import database; db = database(); db.client.drop_database(db.name)"
}

contract() {  # writing the API's contract again must change nothing: it is already current
  local before
  before="$(cat web/openapi.json web/src/api-types.ts | sha256sum)"
  python web/scripts/dump_openapi.py >/dev/null
  (cd web && npx --no-install openapi-typescript openapi.json -o src/api-types.ts >/dev/null)
  [ "$before" = "$(cat web/openapi.json web/src/api-types.ts | sha256sum)" ] || {
    echo "the API changed and its contract had not been written again. It is now: read the diff of" >&2
    echo "web/openapi.json and web/src/api-types.ts, and run the gates again" >&2
    return 1
  }
}

gate "import boundaries" lint-imports
gate "ruff" ruff check .
gate "schema" schema
gate "the API's contract is current" contract
gate "the site builds" npm --prefix web run build --silent
# Four workers that take work from each other: the browser tests are most of the time, and dealt out in
# fixed shares they queued behind one worker while three sat idle (10 min 55 s; CHANGELOG 2026-11-26).
gate "tests" python -m pytest tests/ -q -rs -n "${GATES_WORKERS:-4}" --dist worksteal "${tests[@]}" "$@"
echo "gates: all passed (read the skip count above: a skipped test is not a passing one)"
