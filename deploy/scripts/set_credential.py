"""Store one credential for an account, from the box, without the API running."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from libs.credentials import telegram_chat_id, validate_credential
from libs.db.models import ProxyCredential, User
from libs.db.models.user import SECRET_KINDS
from libs.db.session import session_scope
from libs.obs import configure
from libs.secrets import SecretStore, install, redacting
from services.api.credential_routes import _ensure_proxy_credential


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--kind", required=True, choices=SECRET_KINDS)
    parser.add_argument("--stdin", action="store_true",
                        help="read the value from stdin instead of prompting")
    parser.add_argument("--no-validate", action="store_true",
                        help="store without calling the provider — for when they are down, not for "
                             "when you are unsure")
    parser.add_argument("--chat-id",
                        help="telegram_bot only: set the chat id explicitly instead of reading it. "
                             "Needed when the bot already posts to several chats — getUpdates "
                             "returns whichever spoke last, which may be a channel rather than the "
                             "person")
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

        if args.kind == "proxy":
            # The credential row, not just the secret.
            _ensure_proxy_credential(session, user.id)
            credential = session.get(ProxyCredential, user.id)
            if credential.status != "healthy":
                # A rotation is usually somebody replacing the credential that got quarantined.
                credential.status = "healthy"
                credential.fail_streak = 0
                credential.last_error = None
                print("credential was quarantined — reset to healthy for the new URL")
            print(f"proxy credential ready: {credential.daily_cap_mb} MB/day cap")

        if args.kind == "telegram_bot":
            # The step that confuses people more than the rest of setup put together.
            chat_id = args.chat_id or telegram_chat_id(value)
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
