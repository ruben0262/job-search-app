"""`python -m libs.db [--prune]`: apply the schema to `MONGODB_URI`/`MONGODB_DB` and say what is there.

What a deploy runs before it restarts anything, so a rule or index the database refuses stops the
deploy rather than a service at start-up. `--prune` also drops indexes this code no longer declares:
only the deploy passes it, from an image of the commit being deployed.

First the AI accounts linked before 2026-11-09 are settled (`libs.llm.accounts.settle`), which does nothing
once done: under the rules they were written with, since the new ones refuse any change to a row of a kind
they no longer allow, and the prune changes every row it removes a retired field from. Then the chart's counts
kept before 2026-11-10 are split by source (`libs.ops.countries.split_by_source`), which also does nothing
once done. Then every account from before memberships began is given free access, once ever
(`libs.membership.free_for_existing`), and every account from before alerts were named its "My search" alert,
once ever (`libs.alerts.alerts_for_existing`): before the prune, which removes the switch it reads. And every
alert of an account that had paused them all is switched off, once ever (`libs.alerts.paused_for_existing`),
the pause having gone. And every company board given up before a failed poll was told from a missing board is
asked again, once ever (`libs.corpus.ats.revive_all_once`). And every stored posting's country and town is
read again, once ever, by the reader that knows every town of 5,000 people (`libs.corpus.repository
.read_places_once`). And every posting's match features still inside it are moved beside it
(`libs.fit.move_once`), found by their version, so a deploy after the move reads one index key for it.
"""
from __future__ import annotations

import sys

from libs.alerts import alerts_for_existing, paused_for_existing
from libs.corpus.ats import revive_all_once
from libs.corpus.repository import read_places_once
from libs.db import database, ensure_schema
from libs.db.models import MODELS
from libs.fit import move_once
from libs.llm.accounts import settle
from libs.membership import free_for_existing
from libs.ops.countries import split_by_source


def main() -> int:
    db = database()
    settle(db)
    if split := split_by_source(db):
        print(f"country_counts: {split} count(s) kept before sources were, split by source")
    if freed := free_for_existing(db):
        print(f"users: {freed} account(s) from before memberships given free access")
    if alerted := alerts_for_existing(db):
        print(f"alerts: {alerted} account(s) given their My search alert")
    if paused := paused_for_existing(db):
        print(f"alerts: every alert switched off for {paused} account(s) that had paused them")
    if revived := revive_all_once(db):
        print(f"ats_tokens: {revived} board(s) given up before 2026-11-20 are asked again")
    if placed := read_places_once(db):
        print(f"corpus: {placed} posting(s) given their town, or their country, by the reader of every town")
    if moved := move_once(db):
        print(f"corpus: {moved} posting(s) gave their match features to corpus_fit")
    ensure_schema(db, prune="--prune" in sys.argv[1:])
    for model in MODELS:
        indexes = len(list(db[model.COLLECTION].list_indexes()))
        print(f"{model.COLLECTION:22} {db[model.COLLECTION].estimated_document_count():>8} documents, "
              f"{indexes} index(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
