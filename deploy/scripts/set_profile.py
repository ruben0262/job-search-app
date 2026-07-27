"""Apply a **confirmed** profile to an account, which renders its rubric.

    python deploy/scripts/set_profile.py --email you@example.com --file /tmp/profile.json --dry-run

The file is the output of the confirmation step (`PROFILE_CAPTURE_PLAN.md` §5) — a human has read it
and agreed to it. That is not ceremony: every `not_list` entry becomes a hard gate, and a wrong one
silently suppresses jobs the user would have wanted. A profile whose `confirmed` flag is unset is
refused here for the same reason `save_profile` refuses it.

Takes a path rather than carrying profiles in the repository: a profile is one person's education,
employers, and constraints, and it belongs in the database, not in git.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.contracts.profile import ProfileSchema
from libs.db.models import User
from libs.db.session import session_scope
from libs.scoring.rubric import profile_version, render_rubric
from libs.tenant.repository import TenantRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--show-rubric", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    profile = ProfileSchema.model_validate(json.loads(Path(args.file).read_text(encoding="utf-8")))
    rubric = render_rubric(profile)
    print(f"{args.email}: rubric renders to {len(rubric)} chars, "
          f"profile_version {profile_version(rubric)}")
    print(f"NOT-list ({len(profile.not_list)} hard gates): {profile.not_list}")
    if args.show_rubric:
        print("\n" + rubric)

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    if not profile.confirmed:
        print("ERROR: profile is not marked confirmed — see PROFILE_CAPTURE_PLAN.md §5",
              file=sys.stderr)
        return 2

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2
        generation = TenantRepository(session).save_profile(user.id, profile.model_dump())
        print(f"saved: {args.email} is now on profile_generation {generation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
