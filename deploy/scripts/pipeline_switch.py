"""Pause and resume the parts of the pipeline that spend money or send messages."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from libs.db.session import session_scope

# The lanes worth pausing.
LANES = ("score", "notify", "label", "docgen", "train")


def _ensure_table(session) -> None:
    """The switch table, created on first use."""
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS lane_pauses (
            lane        TEXT PRIMARY KEY,
            paused_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            reason      TEXT
        )"""))


def status(session) -> dict[str, str | None]:
    _ensure_table(session)
    rows = session.execute(text("SELECT lane, reason FROM lane_pauses")).all()
    return dict(rows)


def pause(session, lanes: list[str], reason: str) -> None:
    _ensure_table(session)
    for lane in lanes:
        session.execute(
            text("INSERT INTO lane_pauses (lane, reason) VALUES (:lane, :reason) "
                 "ON CONFLICT (lane) DO UPDATE SET reason = EXCLUDED.reason, paused_at = now()"),
            {"lane": lane, "reason": reason})


def resume(session, lanes: list[str]) -> None:
    _ensure_table(session)
    for lane in lanes:
        session.execute(text("DELETE FROM lane_pauses WHERE lane = :lane"), {"lane": lane})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "pause", "resume"))
    parser.add_argument("lanes", nargs="*", help=f"any of: {', '.join(LANES)}")
    parser.add_argument("--reason", default="paused by an operator")
    args = parser.parse_args(argv)

    unknown = [lane for lane in args.lanes if lane not in LANES]
    if unknown:
        raise SystemExit(f"unknown lane(s): {', '.join(unknown)} — choose from {', '.join(LANES)}")
    if args.action != "status" and not args.lanes:
        raise SystemExit("name at least one lane")

    with session_scope() as session:
        if args.action == "pause":
            pause(session, args.lanes, args.reason)
        elif args.action == "resume":
            resume(session, args.lanes)
        current = status(session)

    for lane in LANES:
        state = f"PAUSED — {current[lane]}" if lane in current else "running"
        print(f"  {lane:8} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
