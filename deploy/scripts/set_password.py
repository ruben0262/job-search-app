"""Set an account's password and mark its email verified."""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.auth import hash_password, password_problems, revoke_all
from libs.db.models import User
from libs.db.session import session_scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    password = getpass.getpass("new password: ")
    if password != getpass.getpass("again: "):
        print("passwords do not match", file=sys.stderr)
        return 2

    problems = password_problems(password, email=args.email)
    if problems:
        print("password " + "; ".join(problems), file=sys.stderr)
        return 2

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2
        user.password_hash = hash_password(password)
        user.status = "active"
        user.active = True
        if user.email_verified_at is None:
            # These accounts were migrated by the operator, not self-registered.
            user.email_verified_at = datetime.now(UTC)
        # A password change ends every existing session.
        revoke_all(session, user.id)
        print(f"password set for {args.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
