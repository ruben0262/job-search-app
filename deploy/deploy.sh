#!/usr/bin/env bash
# Deploy: pull, migrate, build, recreate, and **verify the running code is the code you pushed**.
#
#     ./deploy/deploy.sh            # everything
#     ./deploy/deploy.sh api        # one service, still migrated and verified
#
# **This exists because doing it by hand went wrong four times in one day** (2026-08-01), each in a
# different way, and every one of them looked like success at the time:
#
#   1. code deployed ahead of its migration — every worker 500ed on a column that did not exist yet;
#   2. a migration applied ahead of the code — the same error from the other direction;
#   3. `build` reported success while shipping stale bytecode (see `.dockerignore`);
#   4. `up -d --force-recreate` silently skipped four services that sit behind a compose profile.
#
# The order below is the fix for 1 and 2, `--pull`/`.dockerignore` for 3, and naming every service
# explicitly for 4. The verification step at the end is the fix for *believing* any of it worked.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f deploy/compose.yml -p job-search-platform"

# Every service, named. `up -d` with no arguments skips anything behind a profile, which is how four
# workers stayed on old images while reporting healthy.
#
# **`harvester` is here and was not, which is the same defect one layer along.** It sits behind the
# `tools` profile because it is a one-shot the timer invokes rather than a container that stays up,
# so it was left out of this list — and every full deploy then rebuilt eight services and silently
# left the one that does the collecting on old code. It ran for hours without `fetched_at` after the
# column shipped, reporting healthy cycles the whole time.
ALL_SERVICES="api oauthbroker notifier scorer trainer labeller docgen setupagent capture harvester"

# The one-shots. They exit when their work is done, so "is it running" and `exec` are the wrong
# questions — verification runs a fresh container instead.
ONE_SHOT_SERVICES="harvester"
SERVICES="${*:-$ALL_SERVICES}"

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

say "Pulling"
git pull --ff-only
COMMIT=$(git rev-parse --short HEAD)
echo "at $COMMIT"

say "Migrating"
# Alembic is only in requirements-dev, which no service image carries, so this runs from the host
# venv against the published port. **Before the build, not after**: new code may require a column,
# and a migration is additive far more often than it is destructive.
PW=$($COMPOSE exec -T postgres printenv POSTGRES_PASSWORD | tr -d '\r\n')
DATABASE_URL="postgresql+psycopg://jobsearch:${PW}@127.0.0.1:5432/jobsearch" \
  .venv/bin/python -m alembic upgrade head

say "Building"
# shellcheck disable=SC2086
$COMPOSE build $SERVICES

say "Recreating"
# shellcheck disable=SC2086
$COMPOSE up -d --force-recreate $SERVICES

say "Verifying"
# **The step that would have caught all four.** A container that starts is not a container running
# the code you pushed, so this hashes the `libs` tree inside each service and compares it with the
# host's. A hash rather than a commit marker because it also catches an image built from the right
# commit that still carries stale bytecode — which is the failure that actually happened.
sleep 5
HASH='import hashlib,pathlib
h=hashlib.sha256()
for f in sorted(pathlib.Path("/app/libs").rglob("*.py")):
    h.update(f.read_bytes())
print(h.hexdigest()[:12])'
EXPECTED=$(.venv/bin/python -c "
import hashlib,pathlib
h=hashlib.sha256()
for f in sorted(pathlib.Path('libs').rglob('*.py')):
    h.update(f.read_bytes())
print(h.hexdigest()[:12])")
echo "libs $EXPECTED"

failed=0
for service in $SERVICES; do
  # **Running, before anything else.** A container that is restart-looping still has the right
  # files on disk, so the hash below would have called `oauthbroker` correct while it crashed on
  # an import error every two seconds. State first, contents second.
  # A one-shot has no running container between ticks, so the hash is read from a fresh one.
  if [[ " $ONE_SHOT_SERVICES " == *" $service "* ]]; then
    running=$($COMPOSE run --rm --no-deps --entrypoint python "$service" -c "$HASH" 2>/dev/null       | tr -d '
' || echo unreachable)
    if [ "$running" = "$EXPECTED" ]; then
      printf '  %-12s %s
' "$service" "ok (one-shot)"
    else
      printf '  %-12s [31mrunning %s, expected %s[0m
' "$service" "$running" "$COMMIT"
      failed=1
    fi
    continue
  fi

  state=$($COMPOSE ps "$service" --format '{{.State}}' 2>/dev/null | head -1)
  if [ "$state" != "running" ]; then
    printf '  %-12s \033[31mnot running (%s)\033[0m\n' "$service" "${state:-absent}"
    $COMPOSE logs --tail 5 "$service" 2>&1 | sed 's/^/      /'
    failed=1
    continue
  fi
  running=$($COMPOSE exec -T "$service" python -c "$HASH" 2>/dev/null | tr -d '

' || echo unreachable)
  if [ "$running" = "$EXPECTED" ]; then
    printf '  %-12s %s\n' "$service" "ok"
  else
    printf '  %-12s \033[31mrunning %s, expected %s\033[0m\n' "$service" "$running" "$COMMIT"
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo
  echo "A service is not running the commit you deployed. Rebuild it without cache:"
  echo "  $COMPOSE build --no-cache <service> && $COMPOSE up -d --force-recreate <service>"
  exit 1
fi

say "Deployed $COMMIT"
