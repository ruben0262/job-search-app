"""Evaluate the platform's health and email the operator when something needs them."""
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


# Every timer the platform depends on.
REQUIRED_TIMERS = ("harvester", "linkedin", "notify-dispatch", "health", "backup")

# How long the embedder gets to answer before it counts as down.
EMBEDDER_TIMEOUT_SEC = 20.0


def missing_timers() -> list[str]:
    """Which required timers are not enabled."""
    import shutil
    import subprocess

    if not shutil.which("systemctl"):
        return []
    missing = []
    for unit in REQUIRED_TIMERS:
        result = subprocess.run(["systemctl", "is-enabled", f"{unit}.timer"],
                                capture_output=True, text=True, check=False)
        if result.stdout.strip() != "enabled":
            missing.append(unit)
    return missing


def embedder_down() -> str | None:
    """Why the embedder is unreachable, or `None` if it answers."""
    url = os.environ.get("EMBEDDER_URL")
    if not url:
        return None
    try:
        import httpx
    except ImportError:
        return None
    try:
        response = httpx.get(f"{url.rstrip('/')}/health", timeout=EMBEDDER_TIMEOUT_SEC)
    except Exception as exc:
        return f"{type(exc).__name__} reaching {url}"
    if response.status_code != 200:
        return f"answered {response.status_code}"
    return None


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

    absent = missing_timers()
    embedder = embedder_down()
    summary = render(alerts)
    if embedder:
        # Same reasoning as the timer block: this is the finding that explains the others.
        summary = (
            f"EMBEDDER UNREACHABLE: {embedder}\n"
            "  Without it no new job gets a vector, so nothing is scored and the feed goes quiet\n"
            "  with no other symptom.\n"
            "  Check: docker compose -f deploy/compose.embedder.yml ps\n\n"
            + summary)
    if absent:
        # Prepended rather than appended: it is the finding that explains the others.
        summary = ("TIMERS NOT ENABLED: " + ", ".join(absent)
                   + "\n  Fix with: sudo deploy/install-units.sh\n\n" + summary)
    print(f"{state.corpus_rows:,} corpus rows, {state.users_active} active users, "
          f"{state.postgres_bytes / 1024**3:.2f} GB, {state.connections} connections")
    for lane in state.lanes:
        print(f"  {lane.lane:8} ready={lane.ready:<5} claimed={lane.claimed:<4} "
              f"dead={lane.dead:<4} oldest={lane.oldest_ready_min or 0:.0f}m")
    print()
    print(summary)

    paging = [alert for alert in alerts if alert.pages]
    if embedder:
        # Pages.
        paging.append(f"embedder unreachable: {embedder}")
    if absent:
        # A missing timer pages.
        paging.append(f"timers not enabled: {', '.join(absent)}")
    audit(logger, "health.checked", alerts=len(alerts), paging=len(paging),
          corpus=state.corpus_rows, timers_missing=len(absent),
          embedder_down=bool(embedder))

    threshold_met = (args.email_on == "warn" and alerts) or (args.email_on == "page" and paging)
    if threshold_met and args.to:
        subject = (f"[job-search] {len(paging)} paging" if paging
                   else f"[job-search] {len(alerts)} warning(s)")
        try:
            sender_from_env().send(args.to, subject, summary)
            print(f"\nemailed {args.to}")
        except Exception as exc:
            # The exit code still carries the signal, which is the reason it is the interface.
            print(f"\nALERT EMAIL FAILED ({type(exc).__name__}) — the exit code still reports it",
                  file=sys.stderr)

    return 2 if paging else (1 if alerts else 0)


if __name__ == "__main__":
    raise SystemExit(main())
