"""Store one credential for an account, from the box, without the API running.

    python deploy/scripts/set_credential.py --email you@example.com --kind telegram_bot
    python deploy/scripts/set_credential.py --email you@example.com --kind claude_api --no-validate

The API's `/credentials` endpoint is the real path and this is deliberately not a replacement for
it: it exists because the `api` container is not deployed yet, and an operator setting up the first
two accounts should not have to wait for it.

**The value is read from a prompt, never from an argument.** A credential on a command line ends up
in shell history, in `ps` output for every user on the box, and in any shell tracing that happens to
be on. `--stdin` is offered for scripted use, which keeps it out of all three.

Validated against the provider by default, exactly as the endpoint does — a key that fails here
fails now rather than at 3 a.m. mid-backfill. For `telegram_bot` the chat id is captured
automatically, so nobody has to go and find it.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.credentials import telegram_chat_id, validate_credential
from libs.db.models import User
from libs.db.models.user import SECRET_KINDS
from libs.db.session import session_scope
from libs.obs import configure
from libs.secrets import SecretStore, install, redacting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--kind", required=True, choices=SECRET_KINDS)
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

    with redacting(value), session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
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

        SecretStore(session).put(user.id, args.kind, value, validated=validated)

        if args.kind == "telegram_bot":
            # The step that confuses people more than the rest of setup put together, so it is read
            # for them rather than asked for.
            chat_id = telegram_chat_id(value)
            if chat_id:
                user.telegram_chat_id = chat_id
                user.telegram_status = "connected"
                print(f"chat id captured: {chat_id} — telegram is connected")
            else:
                user.telegram_status = "unconfigured"
                print("no chat id yet: open the bot, press Start, then re-run this command")

    print(f"stored {args.kind} for {args.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
