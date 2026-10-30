"""Set an account's password and mark its email verified."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.auth import hash_password, password_problems, revoke_all
from libs.db import database, find_one, now
from libs.db.models import User


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

    db = database()
    user = find_one(db, User, {"email": args.email.lower()})
    if user is None:
        print(f"no such account: {args.email}", file=sys.stderr)
        return 2
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {
        "password_hash": hash_password(password), "status": "active", "active": True,
        # Set by the operator, so the inbox counts as proven.
        "email_verified_at": user.email_verified_at or now(), "updated_at": now()}})
    # A password change ends every existing session.
    revoke_all(db, user.id)
    print(f"password set for {args.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
