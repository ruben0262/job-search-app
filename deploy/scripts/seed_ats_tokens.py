"""Seed the ATS company list from a reference-instance checkout.

    python deploy/scripts/seed_ats_tokens.py <path-to-checkout>
    python deploy/scripts/seed_ats_tokens.py <path-to-checkout> --dry-run

**Without this, the ATS boards return nothing and say nothing.** Greenhouse, Ashby, Lever, Workday
and the rest are not searchable — there is no query endpoint. Each is polled per *company*, using
that company's board token, so an adapter with no tokens iterates an empty list and returns `[]`.
That is its contract, and it is indistinguishable from a source having no new jobs.

The tokens are a discovered asset, not a configuration choice: the reference accumulated 570 of
them over months, from apply-link resolution and manual additions. They name public job boards and
contain nothing personal — which is why they can be seeded wholesale rather than per user.

**Idempotent.** `add_tokens` upserts, so re-running after the reference has found more is the
intended way to top this up rather than a thing to be careful about.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.corpus.ats import AtsRepository
from libs.db.session import session_scope

# Where the reference keeps them, relative to its checkout root.
TOKENS_JSON = "pipeline/data/ats_tokens.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", help="path to a job-search checkout")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.checkout) / TOKENS_JSON
    if not path.exists():
        raise SystemExit(f"no token file at {path}")

    by_ats: dict[str, list[str]] = json.loads(path.read_text(encoding="utf-8"))
    pairs = [(ats, token) for ats, tokens in by_ats.items() for token in tokens]

    for ats, tokens in sorted(by_ats.items()):
        print(f"  {ats:16} {len(tokens):>4}")
    print(f"\n{len(pairs)} token(s) across {len(by_ats)} board(s)")

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    with session_scope() as session:
        added = AtsRepository(session).add_tokens(pairs)
    print(f"added {added} new token(s); the rest were already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
