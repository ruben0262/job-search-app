#!/usr/bin/env bash
# The whole platform on this computer, in one terminal: the database, the API, the CV maker, a job
# fetch every 15 minutes (as the server's timer does), and the site. Ctrl-C stops all of it.
#
#   scripts/dev.sh        then open http://localhost:5180
#
# Its own port, not Vite's usual 5173: another project's site on 5173 answered instead of this one, and
# the page that opened was not this app's.
# No email is sent from here: a sign-up link prints in this terminal. Needs, once:
# `.venv/bin/pip install -r requirements-dev.txt`, `npm --prefix web install`, and MongoDB 8.0
# unpacked into ~/.local/opt/mongodb (README, "Run it on your computer").
set -euo pipefail
cd "$(dirname "$0")/.."

MONGOD=~/.local/opt/mongodb/bin/mongod
DATA=~/.local/share/job-search-app
for need in .venv/bin/uvicorn web/node_modules "$MONGOD"; do
  [ -e "$need" ] || { echo "missing $need — see the README, \"Run it on your computer\"" >&2; exit 1; }
done

# Keys for this computer only, made once. The server's are in deploy/.env and never come here.
if [ ! -e certificates/dev.env ]; then
  mkdir -p certificates
  (umask 077; .venv/bin/python - > certificates/dev.env <<EOF
import secrets
from libs.secrets import generate_key
print(f"""MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DB=jobsearch_dev
ACCESS_TOKEN_SECRET={secrets.token_urlsafe(48)}
SECRETS_KEK=k0:{generate_key()}
LOG_DIR=$PWD/logs
ARCHIVE_DIR=$PWD/archive
CHROME_BIN=$(ls ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | tail -1)""")
EOF
  )
  echo "made certificates/dev.env"
fi
set -a; . certificates/dev.env; set +a
# Real email, through the same Zoho mailbox as the server, once its password is in this file.
# Without it a sign-up link prints in this terminal instead.
if [ -e certificates/smtp.env ]; then set -a; . certificates/smtp.env; set +a; fi
PORT=5180
export PUBLIC_URL=http://localhost:$PORT  # where the sign-up link points

# Running it again restarts it: the copy started last, in whichever terminal, is stopped first.
PIDFILE="$DATA/dev.pid"
if [ -s "$PIDFILE" ] && ps -o args= -p "$(cat "$PIDFILE")" 2>/dev/null | grep -q 'scripts/dev.sh'; then
  echo "stopping the copy that was already running"
  kill -TERM -- -"$(cat "$PIDFILE")" 2>/dev/null || kill -TERM "$(cat "$PIDFILE")" 2>/dev/null || true
  for _ in $(seq 50); do  # until its ports are free, at most ten seconds
    (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null || (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null \
      || break
    sleep 0.2
  done
fi
mkdir -p "$DATA"
echo $$ > "$PIDFILE"

if ! (exec 3<>/dev/tcp/127.0.0.1/27017) 2>/dev/null; then
  mkdir -p "$DATA/mongo"
  "$MONGOD" --dbpath "$DATA/mongo" --port 27017 --bind_ip 127.0.0.1 --fork \
            --logpath "$DATA/mongod.log" >/dev/null
fi

# Whichever stops first stops the rest, so a crash is never a half-running site.
trap 'kill 0' EXIT
.venv/bin/uvicorn services.api.app:app --port 8000 --reload --reload-dir libs --reload-dir services &
.venv/bin/python -m services.docgen &
(while true; do sleep 60; .venv/bin/python -m services.harvester || true; sleep 840; done) &
npm --prefix web run dev -- --host 127.0.0.1 --port "$PORT" --strictPort &
wait -n
