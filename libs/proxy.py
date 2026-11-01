"""The site's one proxy (owner's decision, 2026-11-01): how the boards that refuse a server's address
— LinkedIn first — are reached, for everyone. Set once on the server, as the marketplace app's is.

`HARVEST_PROXY` takes either form that app takes: `http://user:password@host:port`, or the same
without the scheme. Never logged: it carries the proxy's password.
"""
from __future__ import annotations

import os

ENV = "HARVEST_PROXY"


def site_proxy() -> str | None:
    """The proxy's address, or None when none is set — then nothing that needs one runs."""
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return None
    return raw if "://" in raw else f"http://{raw}"
