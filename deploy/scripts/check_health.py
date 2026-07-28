"""Evaluate the platform's health and email the operator when something needs them.

    python deploy/scripts/check_health.py                # print, exit 0/1/2
    python deploy/scripts/check_health.py --email-on page

Run on a timer (`deploy/health.timer`). **Exit code is the interface**: 0 nothing, 1 warnings, 2 a
page — so a systemd `OnFailure=` or a cron mail catches it even if the mail below never sends.

**Only sends when something is wrong.** A daily "all clear" is an email people filter, and the
filter catches the one that mattered. Silence is the good state, and the timer's own failure is
what `systemctl list-timers` is for.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.db.session import session_scope
from libs.mail import sender_from_env
from libs.obs import audit, configure
from libs.ops import evaluate, platform_health, render

logger = logging.getLogger("health")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email-on", choices=("never", "page", "warn"), default="page",
                        help="lowest severity worth an email (default: page)")
    parser.add_argument("--to", default=os.environ.get("ADMIN_EMAILS", "").split(",")[0].strip(),
                        help="operator address; defaults to the first of ADMIN_EMAILS")
    args = parser.parse_args(argv)

    configure("health")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with session_scope() as session:
        state = platform_health(session)
        alerts = evaluate(state)

    summary = render(alerts)
    print(f"{state.corpus_rows:,} corpus rows, {state.users_active} active users, "
          f"{state.postgres_bytes / 1024**3:.2f} GB, {state.connections} connections")
    for lane in state.lanes:
        print(f"  {lane.lane:8} ready={lane.ready:<5} claimed={lane.claimed:<4} "
              f"dead={lane.dead:<4} oldest={lane.oldest_ready_min or 0:.0f}m")
    print()
    print(summary)

    paging = [alert for alert in alerts if alert.pages]
    audit(logger, "health.checked", alerts=len(alerts), paging=len(paging),
          corpus=state.corpus_rows)

    threshold_met = (args.email_on == "warn" and alerts) or (args.email_on == "page" and paging)
    if threshold_met and args.to:
        subject = (f"[job-search] {len(paging)} paging" if paging
                   else f"[job-search] {len(alerts)} warning(s)")
        try:
            sender_from_env().send(args.to, subject, summary)
            print(f"\nemailed {args.to}")
        except Exception as exc:  # noqa: BLE001 - a failed alert email must not hide the alert
            # The exit code still carries the signal, which is the reason it is the interface.
            print(f"\nALERT EMAIL FAILED ({type(exc).__name__}) — the exit code still reports it",
                  file=sys.stderr)

    return 2 if paging else (1 if alerts else 0)


if __name__ == "__main__":
    raise SystemExit(main())
