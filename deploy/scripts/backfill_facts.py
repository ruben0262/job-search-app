"""Re-render every account's facts block from its stored profile. Does not bump any generation.

    python deploy/scripts/backfill_facts.py

The facts block grounds what is *written* about a person, not what counts as a fit for them, so
changing it cannot make a label wrong. Bumping `profile_generation` here would invalidate every
label and force a retrain for a change that means nothing to either (`PROFILE_CAPTURE_PLAN.md` §7).

Run after a facts-template change, and once for accounts created before the block existed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.db.models import User
from libs.db.session import session_scope
from libs.tenant.repository import TenantRepository


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with session_scope() as session:
        repo = TenantRepository(session)
        for user in session.scalars(select(User).where(User.profile.is_not(None))):
            before = user.profile_generation
            if repo.refresh_facts(user.id):
                print(f"{user.email}: facts block {len(user.facts_md)} chars, "
                      f"generation still {before}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
