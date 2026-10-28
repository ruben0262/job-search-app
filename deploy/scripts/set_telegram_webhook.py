"""Point the platform's one Telegram bot at this site's webhook. Once, and again if either changes.

    set -a && . deploy/.env && set +a
    ./.venv/bin/python deploy/scripts/set_telegram_webhook.py

Reads TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET and PUBLIC_URL. Exit 0 registered, 1 Telegram
refused or could not be reached, 2 something is not set.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from services.api.telegram_routes import router


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    site = os.environ.get("PUBLIC_URL", "").rstrip("/")
    missing = [name for name, value in (("TELEGRAM_BOT_TOKEN", token),
                                        ("TELEGRAM_WEBHOOK_SECRET", secret), ("PUBLIC_URL", site))
               if not value]
    if missing:
        print(f"not set: {', '.join(missing)}", file=sys.stderr)
        return 2

    # Asked of the router rather than written out again, so the two cannot disagree.
    url = site + router.url_path_for("webhook")
    try:
        answer = httpx.post(f"https://api.telegram.org/bot{token}/setWebhook", timeout=15,
                            # Messages only: `/start <code>` is all the webhook acts on.
                            json={"url": url, "secret_token": secret,
                                  "allowed_updates": ["message"]}).json()
    except (httpx.HTTPError, ValueError) as exc:
        # The type only: the request's address carries the token.
        print(f"could not reach Telegram ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not answer.get("ok"):
        print(f"Telegram refused: {answer.get('description')}", file=sys.stderr)
        return 1
    print(f"registered: Telegram now posts to {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
