"""What every request passes on its way in, and every answer on its way out: how large a body may be, what a
browser is told to hold the site to, and how long it may keep what it was sent.

One wrapper round the whole app, the site's files with it, because nothing else sees every answer. Until
2026-11-20 there was none: a body of any size was read into memory before anybody was asked who they were,
in a container of 1 GiB; no answer said the site may not be framed or its files' types guessed; and the
site's own files went out with no word on keeping them, so a browser could go on using a page that named
files a deploy had since removed.
"""
from __future__ import annotations

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

# Told with every answer. The site is reached over HTTPS alone and stays so for a year; a file is the type it
# says it is; nobody else's page may frame it, or point its links or plug-ins elsewhere; and another site is
# told where a visitor came from, not which page. Not yet a policy on scripts and styles: one that is wrong
# stops the payment form, and that needs trying against Stripe in a browser first (ROADMAP 6d).
HEADERS = (
    (b"strict-transport-security", b"max-age=31536000"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'; base-uri 'self'; object-src 'none'"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)


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
        # The page and its worker are asked for again each time: they name the files of this deploy.
        return ((b"cache-control", b"no-cache"),)
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
