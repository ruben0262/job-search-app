"""What every request passes on its way in, and every answer on its way out: how large a body may be, what a
browser is told to hold the site to, and how long it may keep what it was sent.

One wrapper round the whole app, the site's files with it, because nothing else sees every answer. Until
2026-11-20 there was none: a body of any size was read into memory before anybody was asked who they were,
in a container of 1 GiB; no answer said the site may not be framed or its files' types guessed; and the
site's own files went out with no word on keeping them, so a browser could go on using a page that named
files a deploy had since removed.
"""
from __future__ import annotations

import re

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from libs.documents import MAX_FILE_BYTES

# The largest body a request may carry. The longest thing typed here is "About you", 400,000 characters
# (`onboarding_routes`): 1.6 MB at four bytes each.
MAX_BODY_BYTES = 2 * 1024 * 1024
# An upload is a file of up to `MAX_FILE_BYTES` and the form it comes in.
BODY_LIMITS = {"/documents": MAX_FILE_BYTES + 1024 * 1024}
# HTTP's answer to a body that is too large.
TOO_LARGE = 413

# What a page of this site may load and run, told with every answer. Its own files, and Stripe's for the
# payment form, each origin as Stripe's own list has it (docs.stripe.com/security/guide, read 2026-11-20:
# Stripe.js, Checkout and Link). No script that is not a file: the one the page wrote into itself is
# `web/public/theme.js` now. Styles written into a page stay allowed, because a look's picture is drawn from
# markup that carries its own (`web/src/templates.tsx`). Nobody else's page may frame the site, or point its
# links or plug-ins elsewhere.
_STRIPE = "https://js.stripe.com https://*.js.stripe.com"
_STRIPE_LINK = "https://link.com https://*.link.com"
POLICY = "; ".join((
    "default-src 'self'",
    f"script-src 'self' {_STRIPE} https://checkout.stripe.com",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://*.stripe.com https://*.link.com",
    "font-src 'self'",
    f"connect-src 'self' https://api.stripe.com https://checkout.stripe.com {_STRIPE_LINK}",
    f"frame-src {_STRIPE} https://hooks.stripe.com https://checkout.stripe.com {_STRIPE_LINK}",
    "worker-src 'self'",
    "manifest-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
))

# Told with every answer. The site is reached over HTTPS alone and stays so for a year; a file is the type it
# says it is; and another site is told where a visitor came from, not which page.
HEADERS = (
    (b"strict-transport-security", b"max-age=31536000"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", POLICY.encode()),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)


# The API's GET addresses as patterns, each with whether it answers a browser itself: found once, from the
# contract.
_GETS: list[tuple[re.Pattern, bool]] | None = None


def _api_gets(path: str) -> list[bool]:
    """For each of the API's GET addresses this path is, whether the API answers a browser there itself: a
    file to download, or the way back from signing in somewhere else. Read off the contract, not listed: a
    GET whose answer, as the contract states it, is never JSON."""
    global _GETS  # noqa: PLW0603 - found once, at the first page asked for
    if _GETS is None:
        from services.api.app import app  # noqa: PLC0415 - the app imports this module

        _GETS = [(re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", route) + "$"),
                  not any("application/json" in (answer.get("content") or {})
                          for code, answer in item["get"]["responses"].items() if code[0] in "23"))
                 for route, item in app.openapi()["paths"].items() if "get" in item]
    return [own for pattern, own in _GETS if pattern.match(path)]


def page_asked(scope: Scope) -> bool:
    """Whether this is a request for one of the site's pages. A page's address and the data behind it are
    often the same words (`/applied`, `/alerts`): a browser going there is given the page, and the page's
    own code asking is given the data, told apart by what is asked for (`Accept`). Every other address is a
    page, whatever the request says it takes: bingbot asks with `*/*`, and was given a 404 for every page
    but the first (SEO audit, 2026-11-25). Asked with HEAD too, as a link checker or an uptime monitor asks.
    An address that names a file, one under `/.well-known/` (machines' addresses, never a page), and one the
    API answers a browser at itself, are left as they are."""
    path = scope["path"]
    if scope["method"] not in ("GET", "HEAD") or "." in path.rsplit("/", 1)[-1] \
            or path.startswith("/.well-known/"):
        return False
    api = _api_gets(path)
    return not api or (b"text/html" in dict(scope["headers"]).get(b"accept", b"") and not any(api))


def _keeping(path: str, start: Message) -> tuple[tuple[bytes, bytes], ...]:
    """How long a browser may keep this answer, where the answer does not say."""
    kind = dict(start.get("headers", [])).get(b"content-type", b"")
    if path.startswith("/assets/"):
        # Named by what is in it (Vite): the same address is the same file for good.
        return ((b"cache-control", b"public, max-age=31536000, immutable"),) if start["status"] == 200 else ()
    if kind.startswith((b"application/json", b"text/event-stream")):
        # Somebody's own data, or an error about it: kept by no browser and no proxy.
        return ((b"cache-control", b"no-store"),)
    if kind.startswith(b"text/html") or path == "/sw.js":
        # The page and its worker are asked for again each time: they name the files of this deploy. And an
        # address answers a browser with the page and the page's code with data, and a crawler with the page
        # as drawn (`page_routes`), so nothing between may hand one the other's.
        return ((b"cache-control", b"no-cache"), (b"vary", b"Accept, Sec-Fetch-Dest"))
    return ()


class Guard:
    """The wrapper: a body past its limit is refused, by its stated length before a byte of it is read, or
    as it arrives where it states none; and each answer leaves with `HEADERS` and how long to keep it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if page_asked(scope):
            # Every page is the one file, which draws the page its address names (`web/src/route.ts`), told
            # the address so its head can say which page it is (`page_routes`).
            scope = {**scope, "path": "/", "raw_path": b"/",
                     "state": {**scope.get("state", {}), "page": scope["path"]}}
        path = scope["path"]
        limit = BODY_LIMITS.get(path, MAX_BODY_BYTES)
        too_large = f"That is more than the {limit // (1024 * 1024)} MB this takes."
        taken = 0

        async def counted() -> Message:
            nonlocal taken
            message = await receive()
            if message["type"] == "http.request":
                taken += len(message.get("body", b""))
                if taken > limit:
                    raise HTTPException(TOO_LARGE, detail=too_large)
            return message

        async def headed(message: Message) -> None:
            if message["type"] == "http.response.start":
                given = list(message.get("headers", []))
                have = {name.lower() for name, _ in given}
                more = [pair for pair in (*HEADERS, *_keeping(path, message)) if pair[0] not in have]
                message = {**message, "headers": [*given, *more]}
            await send(message)

        stated = dict(scope["headers"]).get(b"content-length", b"")
        if stated.isdigit() and int(stated) > limit:
            await JSONResponse({"detail": too_large}, status_code=TOO_LARGE)(scope, receive, headed)
            return
        await self.app(scope, counted, headed)
