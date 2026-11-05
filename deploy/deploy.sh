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

# Every compose command through the one wrapper, which reads certificates/server.env.
COMPOSE="deploy/compose.sh"

# Every service, named. `up -d` with no arguments skips anything behind a profile, which is how four
# workers stayed on old images while reporting healthy.
#
# **`harvester` is here and was not, which is the same defect one layer along.** It sits behind the
# `tools` profile because it is a one-shot the timer invokes rather than a container that stays up,
# so it was left out of this list — and every full deploy then rebuilt eight services and silently
# left the one that does the collecting on old code. It ran for hours without `fetched_at` after the
# column shipped, reporting healthy cycles the whole time.
ALL_SERVICES="api oauthbroker notifier docgen harvester describer seed-ats"

# The one-shots. They exit when their work is done, so "is it running" and `exec` are the wrong
# questions — verification runs a fresh container instead.
ONE_SHOT_SERVICES="harvester seed-ats"
SERVICES="${*:-$ALL_SERVICES}"

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

say "Pulling"
BEFORE=$(git rev-parse HEAD)
git pull --ff-only
COMMIT=$(git rev-parse --short HEAD)
echo "at $COMMIT"
# Made by the account deploying, so the services can write them; left to Docker, they are root's.
mkdir -p logs archive

say "Building"
# `api` always, whatever is being deployed: the schema step below runs in it, and it must be this
# commit's code that decides which indexes exist.
# shellcheck disable=SC2086
$COMPOSE build $SERVICES api

say "Schema"
# In the image just built, against MongoDB, **before anything restarts**: a rule or index the database
# refuses stops the deploy here instead of a service at start-up (`libs/db/__main__.py`).
# `--entrypoint python`: the image's entrypoint is uvicorn, and `run api python …` handed these words to
# uvicorn as arguments ("No such option '-m'"), stopping every deploy here (found 2026-11-03).
$COMPOSE run --rm --no-deps --entrypoint python api -m libs.db --prune

say "Recreating"
# The services that stay up. A one-shot is only built: its next run (the timer's, or deploy's seed
# below) uses the new image. Named to `up`, each was started there and then, so every full deploy ran
# a harvest and a whole company-board seed, a quarter of an hour of asking every ATS (found 2026-11-03).
RUNNING=""
for service in $SERVICES; do
  [[ " $ONE_SHOT_SERVICES " == *" $service "* ]] || RUNNING="$RUNNING $service"
done
if [ -n "$RUNNING" ]; then
  # shellcheck disable=SC2086
  $COMPOSE up -d --force-recreate $RUNNING
fi

# The tunnel: started if it is not running, recreated only if its settings changed (a new token), and
# otherwise left alone. Restarting it would drop the site's only way in for nothing: none of its code
# is this repository's.
say "Tunnel"
$COMPOSE up -d --no-deps cloudflared

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
EXPECTED=$(python3 -c "
import hashlib,pathlib
h=hashlib.sha256()
for f in sorted(pathlib.Path('libs').rglob('*.py')):
    h.update(f.read_bytes())
print(h.hexdigest()[:12])")
echo "libs $EXPECTED"

# `down`: a service that is not running. `stale`: one running other code than this commit's.
failed=0 down=0 stale=0
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
      failed=1 stale=1
    fi
    continue
  fi

  state=$($COMPOSE ps "$service" --format '{{.State}}' 2>/dev/null | head -1)
  if [ "$state" != "running" ]; then
    printf '  %-12s \033[31mnot running (%s)\033[0m\n' "$service" "${state:-absent}"
    $COMPOSE logs --tail 12 "$service" 2>&1 | sed 's/^/      /'
    failed=1 down=1
    continue
  fi
  running=$($COMPOSE exec -T "$service" python -c "$HASH" 2>/dev/null | tr -d '

' || echo unreachable)
  if [ "$running" = "$EXPECTED" ]; then
    printf '  %-12s %s\n' "$service" "ok"
  else
    printf '  %-12s \033[31mrunning %s, expected %s\033[0m\n' "$service" "$running" "$COMMIT"
    failed=1 stale=1
  fi
done

tunnel=$($COMPOSE ps cloudflared --format '{{.State}}' 2>/dev/null | head -1)
if [ "$tunnel" = "running" ]; then
  printf '  %-12s %s\n' "cloudflared" "ok (running)"
else
  printf '  %-12s \033[31mnot running (%s)\033[0m: is TUNNEL_TOKEN set in certificates/server.env?\n' \
    "cloudflared" "${tunnel:-absent}"
  failed=1 down=1
fi

# The address the emails link to, as the running site has it, opened from the internet. Not reaching
# it is a failed deploy: nobody can verify a sign-up or reset a password (found 2026-11-03).
deploy/reach.sh "$($COMPOSE exec -T api python -c 'from libs.notify import site_url; print(site_url())')" \
  || failed=1

if [ "$failed" -ne 0 ]; then
  echo
  # Not running is its own code failing, said in the log lines above; a rebuild would build the same.
  if [ "$down" -ne 0 ]; then
    echo "A service is not running: why is in its log above (the rest: $COMPOSE logs <service>)."
  fi
  if [ "$stale" -ne 0 ]; then
    echo "A service is not running the commit you deployed. Rebuild it without cache:"
    echo "  $COMPOSE build --no-cache <service> && $COMPOSE up -d --force-recreate <service>"
  fi
  exit 1
fi

# The company list changed in what was just pulled (or arrived with it): look for its boards again. In
# the background, because a full run asks eight ATS about every name and takes a quarter of an hour;
# it writes only `ats_tokens`, so nothing waits on it. Its report is logs/seed_ats.log.
if [[ " $SERVICES " == *" seed-ats "* ]] \
    && ! git diff --quiet "$BEFORE" "$COMMIT" -- deploy/scripts/ats_companies.txt; then
  say "The company list changed: seeding its boards in the background"
  $COMPOSE run -d --rm --no-deps seed-ats
fi

say "Deployed $COMMIT"
