"""Combine the results of several `run.py` invocations into one scoreboard.

The grid is split across processes — one per candidate per variant — because a cell is a full CV
generation plus a judge call and runs about two minutes, so 72 of them in one process is three
hours of wall clock. Each process writes its own `results.json`; this concatenates them and runs
the same `scoreboard()` the single-process path would have produced, so splitting the run changes
nothing about the comparison.

    python scripts/cvlab/merge.py scripts/cvlab/out/ab --out scripts/cvlab/out/ab
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.cvlab.run import scoreboard  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="directory holding the per-arm result folders")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    out = pathlib.Path(args.out or args.root)
    rows: list[dict] = []
    for path in sorted(root.glob("*/results.json")):
        found = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(found)
        print(f"{path.parent.name}: {len(found)} rows")

    if not rows:
        print("no results.json under", root)
        return 1

    # Same cell measured twice would silently double-weight one arm of the comparison.
    seen = {(row["candidate"], row["variant"], row["job"], row["sample"]) for row in rows}
    if len(seen) != len(rows):
        print(f"WARNING: {len(rows) - len(seen)} duplicate cell(s) across folders")

    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    board = scoreboard(rows)
    (out / "scoreboard.md").write_text(board, encoding="utf-8")
    print()
    print(board)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
