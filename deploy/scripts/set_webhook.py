"""Point a user's Telegram bot at the platform's webhook.

    python deploy/scripts/set_webhook.py --email you@example.com --base-url https://app.example.com
    python deploy/scripts/set_webhook.py --email you@example.com --delete

Generates the per-user path secret if the account has none, registers it with Telegram, and passes
the same value as Telegram's `secret_token` so both halves of the check line up
(`services/api/telegram_routes.py`).

**Idempotent, and it rotates on demand.** Re-running with `--rotate` issues a new secret and
re-registers — which is what to do if the URL is ever exposed, since the path is half the
authentication.

`allowed_updates` is narrowed to `callback_query`: the bot has no reason to receive every message in
a channel, and not receiving them means never having to be careful with them.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from sqlalchemy import select

from libs.db.models import User
from libs.db.session import session_scope
from libs.obs import configure
from libs.secrets import SecretStore, install, redacting

TIMEOUT = 20.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--base-url", help="public origin of the API, e.g. https://app.example.com")
    parser.add_argument("--rotate", action="store_true", help="issue a new secret and re-register")
    parser.add_argument("--delete", action="store_true", help="unregister the webhook")
    args = parser.parse_args(argv)

    if not args.delete and not args.base_url:
        print("--base-url is required unless --delete", file=sys.stderr)
        return 2

    configure("tools")
    install()

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2

        with SecretStore(session).use(user.id, "telegram_bot") as secret:
            if secret is None:
                print("no telegram token stored for this account", file=sys.stderr)
                return 2
            token = secret.reveal()

            with redacting(token):
                if args.delete:
                    response = httpx.post(f"https://api.telegram.org/bot{token}/deleteWebhook",
                                          timeout=TIMEOUT).json()
                    user.telegram_webhook_secret = None
                    print("deleted" if response.get("ok") else response.get("description"))
                    return 0 if response.get("ok") else 1

                if args.rotate or not user.telegram_webhook_secret:
                    # 32 bytes of urlsafe randomness. It is both a URL path segment and a header
                    # value, so it has to survive both without escaping.
                    user.telegram_webhook_secret = secrets.token_urlsafe(32)[:64]
                    session.flush()

                url = f"{args.base_url.rstrip('/')}/telegram/webhook/{user.telegram_webhook_secret}"
                response = httpx.post(
                    f"https://api.telegram.org/bot{token}/setWebhook",
                    json={
                        "url": url,
                        "secret_token": user.telegram_webhook_secret,
                        # Only button taps. The bot has no reason to receive every channel message,
                        # and not receiving them means never having to be careful with them.
                        "allowed_updates": ["callback_query"],
                        # Anything queued from before this registration is stale by definition.
                        "drop_pending_updates": True,
                    },
                    timeout=TIMEOUT).json()

        if not response.get("ok"):
            print(f"Telegram refused: {response.get('description')}", file=sys.stderr)
            return 1

    # The secret is deliberately not printed: it is half the authentication, and a terminal
    # scrollback is not where it belongs.
    print(f"webhook registered for {args.email} at {args.base_url.rstrip('/')}/telegram/webhook/…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
