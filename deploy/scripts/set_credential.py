"""Link one AI API key to an account, from the box, without the API running. It goes in use, as one
linked in Settings does (`libs.llm.accounts`). Sign-ins to a Claude, ChatGPT or Google account are made in
Settings only: they run the provider's own tool."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.credentials import validate_credential
from libs.db import database, find_one
from libs.db.models import User
from libs.db.models.user import KEY_KINDS
from libs.llm import accounts
from libs.obs import configure
from libs.secrets import SecretStore, install, redacting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--kind", required=True, choices=KEY_KINDS)
    parser.add_argument("--stdin", action="store_true",
                        help="read the value from stdin instead of prompting")
    parser.add_argument("--no-validate", action="store_true",
                        help="store without calling the provider — for when they are down, not for "
                             "when you are unsure")
    args = parser.parse_args(argv)

    configure("tools")
    install()

    value = (sys.stdin.readline() if args.stdin else getpass.getpass(f"{args.kind}: ")).strip()
    if not value:
        print("nothing entered", file=sys.stderr)
        return 2

    db = database()
    with redacting(value):
        user = find_one(db, User, {"email": args.email.lower()})
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2

        validated = False
        if not args.no_validate:
            result = validate_credential(args.kind, value)
            validated = result.ok
            print(f"validation: {'ok' if result.ok else 'FAILED'} — {result.detail}")
            if not result.ok:
                print("refusing to store. Re-run with --no-validate if you are certain and the "
                      "provider is simply unreachable.", file=sys.stderr)
                return 3

        account = SecretStore(db).add(user.id, args.kind, value, label=accounts.KIND_NAMES[args.kind],
                                      validated=validated)
        accounts.use(db, user.id, str(account))

    print(f"linked {args.kind} for {args.email}, in use")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
