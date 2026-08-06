"""Embed every corpus row that is still missing a vector."""
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
    parser.add_argument("--batch-size", type=int, default=64,
                        help="texts per bulk call; smaller means more frequent commits")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds to wait for one bulk call")
    args = parser.parse_args(argv)

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    if not args.url:
        print("EMBEDDER_URL is not set", file=sys.stderr)
        return 2

    # The client's 120 s default is sized for the harvester's small batches.
    client = EmbedderClient(args.url, timeout=args.timeout)
    # Built exactly as services/harvester/__main__ builds it.
    expected = EmbedderVersion(
        model=os.environ.get("EMBEDDER_MODEL", "BAAI/bge-base-en-v1.5"),
        dim=int(os.environ.get("EMBEDDER_DIM", "768")),
        version=os.environ.get("EMBEDDER_VERSION", ""))

    total = 0
    for pass_number in range(1, args.batches + 1):
        with session_scope() as session:
            # One batch per pass, so one commit per batch.
            result = embed_all_pending(CorpusRepository(session), client, expected=expected,
                                       max_batches=1, batch_size=args.batch_size)
        total += result.embedded
        print(f"pass {pass_number}: embedded {result.embedded} (running total {total})", flush=True)
        if result.embedded == 0:
            print("nothing left to embed")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
