"""Point a user's per-category Telegram channels at real chat ids."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from sqlalchemy import select

from libs.db.models import User
from libs.db.session import session_scope
from libs.obs import configure
from libs.secrets import SecretStore, install
from services.notifier.routing import ROUTES


def _reachable(token: str, chat_id: str) -> tuple[bool, str]:
    """Ask Telegram what it thinks of this chat."""
    try:
        response = httpx.get(f"https://api.telegram.org/bot{token}/getChat",
                             params={"chat_id": chat_id}, timeout=15)
        body = response.json()
    except Exception as exc:
        return False, f"could not reach Telegram: {type(exc).__name__}"
    if not body.get("ok"):
        return False, str(body.get("description") or "refused")
    title = (body.get("result") or {}).get("title") or "(no title)"
    return True, title


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    for route in ROUTES:
        parser.add_argument(f"--{route}", help=f"chat id for the {route!r} stream")
    parser.add_argument("--clear", action="store_true",
                        help="remove every route — delivery returns to one personal chat")
    parser.add_argument("--no-check", action="store_true",
                        help="skip asking Telegram whether the bot can see each channel")
    args = parser.parse_args(argv)

    configure("tools")
    install()

    given = {route: getattr(args, route) for route in ROUTES if getattr(args, route)}
    if not given and not args.clear:
        print("nothing to do: pass at least one route, or --clear", file=sys.stderr)
        return 2

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2

        if args.clear:
            user.telegram_routes = None
            print(f"cleared routes for {args.email} — everything goes to the personal chat")
            return 0

        if not args.no_check:
            with SecretStore(session).use(user.id, "telegram_bot") as secret:
                if secret is None:
                    print("no telegram token stored — set that first", file=sys.stderr)
                    return 2
                token = secret.reveal()
                for route, chat_id in given.items():
                    ok, detail = _reachable(token, chat_id)
                    print(f"  {route:9} {chat_id:>18}  {'ok' if ok else 'FAILED'} — {detail}")
                    if not ok:
                        print("refusing to store a route the bot cannot see. Add the bot to the "
                              "channel as an administrator, or pass --no-check.", file=sys.stderr)
                        return 3

        # Merged, not replaced: setting one route should not silently drop the other two.
        user.telegram_routes = {**(user.telegram_routes or {}), **given}
        print(f"routes for {args.email}: {user.telegram_routes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
