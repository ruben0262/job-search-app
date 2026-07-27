"""What the shared harvest is actually doing, across every active account. Read-only.

The D6 comparison needs a way to see the thing that makes the shared design worth having: two
users' configs folding into one query set, with the overlap paid for once. This prints that.

    python deploy/scripts/show_query_set.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.db.session import session_scope
from libs.tenant.repository import TenantRepository
from services.harvester.query_set import (
    HARVEST_MAX_QUERIES_PER_CYCLE,
    build_query_set,
    select_within_budget,
)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with session_scope() as session:
        configs = TenantRepository(session).active_configs()
        print(f"active accounts: {len(configs)}")
        for user_id, config in configs.items():
            print(f"  {user_id}  {len(config['keywords'])} terms  "
                  f"locations={config['locations']}")

        query_set = build_query_set(configs, sources=("indeed", "google"))
        shared = sum(1 for users in query_set.values() if len(users) > 1)
        print(f"\nfolded query set: {len(query_set)} distinct queries, "
              f"{shared} wanted by more than one account")

        planned, deferred = select_within_budget(
            query_set, max_queries=HARVEST_MAX_QUERIES_PER_CYCLE)
        print(f"budget: {len(planned)} planned per cycle, {len(deferred)} deferred to the next")

        served: dict = {}
        for query in planned:
            for user_id in query_set[query.key]:
                served[user_id] = served.get(user_id, 0) + 1
        for user_id, count in served.items():
            print(f"  {user_id}: {count} planned queries serve this account")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
