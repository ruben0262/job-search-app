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

# **Three timers, down from five** (2026-08-05, ROADMAP 6ar). Every clock is a unit to install, a
# timeout to get wrong and a thing that can silently not be running — all three of which have
# happened here — so a job that can be done by a process already looping is not given a timer.
#
#   harvester   — one fetch tick. The shared cycle across every source, then the per-user LinkedIn
#                 pass on the same run. Missing: no new jobs enter the corpus at all.
#   health      — the check that notices the above, and emails on a page. Missing: nobody finds out.
#   backup      — pg_dump at 03:20. Missing: one bad day is unrecoverable.
#
# **Health stays separate on purpose, and it is the one reduction not worth making.** Folding it
# into a pipeline worker would mean the check dies with the thing it is checking, which is precisely
# when it is needed. A monitor inside the monitored process reports "healthy" right up until it
# reports nothing.
#
# Removed, and their leftovers must be cleaned off any box that ran the old version:
#
#   notify-dispatch — the notifier worker is long-lived and now dispatches on its own loop. It was
#                     absent from this box for weeks in July, so nothing entered the notify lane
#                     while every container looked healthy.
#   linkedin        — folded into the harvest tick. Its `TimeoutStartSec=30min` against a
#                     fifteen-minute timer meant a stuck pass cancelled the following tick.
#   linkedin-describe — describing moved inside the LinkedIn pass on 2026-08-03 (ROADMAP 6l.1).
#
#     sudo systemctl disable --now notify-dispatch.timer linkedin.timer linkedin-describe.timer
#     sudo rm -f /etc/systemd/system/{notify-dispatch,linkedin,linkedin-describe}.{service,timer}
#     sudo systemctl daemon-reload
TIMERS=(harvester health backup)

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
