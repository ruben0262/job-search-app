"""Pause and resume the parts of the pipeline that spend money or send messages."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymongo.database import Database

from libs.db import database, now
from libs.db.models import LANES, LanePause


def status(db: Database) -> dict[str, str | None]:
    return {row["_id"]: row.get("reason") for row in db[LanePause.COLLECTION].find()}


def pause(db: Database, lanes: list[str], reason: str) -> None:
    for lane in lanes:
        db[LanePause.COLLECTION].update_one(
            {"_id": lane}, {"$set": {"reason": reason, "paused_at": now()}}, upsert=True)


def resume(db: Database, lanes: list[str]) -> None:
    db[LanePause.COLLECTION].delete_many({"_id": {"$in": lanes}})


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

    db = database()
    if args.action == "pause":
        pause(db, args.lanes, args.reason)
    elif args.action == "resume":
        resume(db, args.lanes)
    current = status(db)

    for lane in LANES:
        state = f"PAUSED — {current[lane]}" if lane in current else "running"
        print(f"  {lane:8} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
