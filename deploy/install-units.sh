#!/usr/bin/env bash
# Install and enable every systemd unit this platform needs. Idempotent — run it after any deploy.
#
#     sudo deploy/install-units.sh
#
# **This exists because the manual version silently half-worked for weeks.** On 2026-07-29 the box
# had `harvester` and `backup` running, `health` installed but disabled, and `notify-dispatch` and
# `linkedin-describe` not installed at all. The notify dispatcher is the one that matters: nothing
# else puts jobs in the `notify` lane, so its absence meant the worker sat idle forever while the
# stack looked completely healthy — containers up, no errors, no user ever hearing from us.
#
# Why one script rather than one merged timer: each unit is a separate one-shot with its own log,
# its own exit code and its own timeout, and a crash in one does not stop the others. Folding them
# into a single long-running scheduler would trade that for exactly the silent-death failure the
# one-shot design was chosen to avoid. What was actually broken was *installation*, so that is what
# is automated — and `check_health.py` now asserts the result rather than trusting it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR=/etc/systemd/system

# Every timer the platform runs, and what breaks when it is missing.
#   harvester         — the shared cycle. Missing: no new jobs enter the corpus.
#   notify-dispatch   — fills the notify lane. Missing: nothing is ever delivered.
#   linkedin          — per-user LinkedIn fetch. Missing: no LinkedIn jobs enter the corpus at all.
#   linkedin-describe — per-user proxied metadata. Missing: LinkedIn rows rank blind.
#   health            — the check that notices the above. Missing: nobody finds out.
#   backup            — pg_dump. Missing: one bad day is unrecoverable.
TIMERS=(harvester linkedin notify-dispatch linkedin-describe health backup)

if [[ $EUID -ne 0 ]]; then
  echo "needs root: sudo $0" >&2
  exit 1
fi

for unit in "${TIMERS[@]}"; do
  install -m 0644 "$REPO/deploy/$unit.service" "$UNIT_DIR/$unit.service"
  install -m 0644 "$REPO/deploy/$unit.timer" "$UNIT_DIR/$unit.timer"
done

systemctl daemon-reload

for unit in "${TIMERS[@]}"; do
  systemctl enable --now "$unit.timer"
done

echo
echo "installed and enabled:"
systemctl list-timers "${TIMERS[@]/%/.timer}" --no-pager
