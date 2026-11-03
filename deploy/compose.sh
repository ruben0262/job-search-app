#!/usr/bin/env bash
# `docker compose` for this platform, with its settings from certificates/server.env. Every command
# on the server goes through this (deploy.sh, the harvest timer, and anything typed by hand), so they
# all read the one file. The secrets live in certificates/, as the laptop's dev.env does, never in
# deploy/ (owner, 2026-11-03). Arguments are compose's own:
#
#     deploy/compose.sh ps
#     deploy/compose.sh logs -f api
#     deploy/compose.sh run --rm --no-deps harvester
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/certificates/server.env"
if [ ! -r "$ENV_FILE" ]; then
  echo "no $ENV_FILE: copy deploy/server.env.example there, fill it in, then chmod 600 it" >&2
  exit 1
fi

# The Claude sign-in broker runs as the account that owns the checkout, so it can write to logs/
# (Dockerfile.oauthbroker). Read off the directory, so it is right under sudo and systemd too.
export BROKER_UID="${BROKER_UID:-$(stat -c %u "$ROOT")}"

exec docker compose --env-file "$ENV_FILE" -f "$ROOT/deploy/compose.yml" -p job-search-platform "$@"
