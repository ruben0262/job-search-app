"""A person's data, as the Privacy Policy and the Terms promise it on request (GDPR Articles 15 and 20; Terms
section 16.2): one JSON file of everything stored about their account, by `libs.db.export_account`. Uploaded
files and made documents are in it as base64; passwords and sealed credentials are not.

    ./.venv/bin/python deploy/scripts/export_account.py person@example.com > mahoraga-data.json

Send the file to the address on the account, and only to it.
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.db import database, export_account, find_one
from libs.db.models import User


def _plain(value: object) -> str:
    """What JSON has no word for, in one it has, so the file reads in any tool: times in ISO 8601, files in
    base64, ids as text."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return str(value)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    db = database()
    user = find_one(db, User, {"email": args[0].strip().lower()})
    if user is None:
        print(f"no account has the address {args[0]}", file=sys.stderr)
        return 1
    print(json.dumps(export_account(db, user.id), default=_plain, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
