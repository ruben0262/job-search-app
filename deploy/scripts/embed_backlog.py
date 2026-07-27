"""Embed every corpus row that is still missing a vector. Bounded, resumable, idempotent.

    python deploy/scripts/embed_backlog.py --batches 40

The harvest cycle embeds what it just ingested, but a history import drops several thousand rows in
at once — more than one cycle should carry, and the cycle runs under a 14-minute systemd timeout.
This does the same work through the same path, in bounded passes that can be stopped and resumed.

Uses the **bulk** lane deliberately: the fast lane exists so a user-facing score is not stuck behind
a backfill, and running a backfill through it would defeat exactly that
(`services/embedder/README.md`).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.contracts.embedder import EmbedderClient, EmbedderVersion
from libs.corpus.repository import CorpusRepository
from libs.db.session import session_scope
from services.harvester.embedding import embed_all_pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches", type=int, default=20,
                        help="how many passes to run before exiting; re-run to continue")
    parser.add_argument("--url", default=os.environ.get("EMBEDDER_URL"))
    args = parser.parse_args(argv)

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    if not args.url:
        print("EMBEDDER_URL is not set", file=sys.stderr)
        return 2

    client = EmbedderClient(args.url)
    # Built exactly as services/harvester/__main__ builds it, so a backfill cannot write vectors
    # the harvester would have refused.
    expected = EmbedderVersion(
        model=os.environ.get("EMBEDDER_MODEL", "BAAI/bge-base-en-v1.5"),
        dim=int(os.environ.get("EMBEDDER_DIM", "768")),
        version=os.environ.get("EMBEDDER_VERSION", ""))

    total = 0
    for pass_number in range(1, args.batches + 1):
        with session_scope() as session:
            # One batch per pass, so one commit per batch. `embed_all_pending` otherwise runs up to
            # 50 batches inside a single transaction and commits at the end — which is right for the
            # harvester (a cycle is atomic) and wrong here: at roughly two minutes per batch of 64,
            # a backlog of a few thousand rows is hours of work that a dropped SSH session or a
            # restart would throw away entirely. Learned the expensive way.
            result = embed_all_pending(CorpusRepository(session), client,
                                       expected=expected, max_batches=1)
        total += result.embedded
        print(f"pass {pass_number}: embedded {result.embedded} (running total {total})", flush=True)
        if result.embedded == 0:
            print("nothing left to embed")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
