"""The one process that owns Claude sign-in sessions.

**Why this is a service and not three functions in the API.** `claude setup-token` is interactive:
it prints a URL, blocks on stdin, and prints the token only after the code comes back. The PKCE
verifier lives in that process's memory, so the request that hands out the URL and the request that
brings the code back must reach *the same* process. Inside the API that made sessions accidentally
correct — correct only while exactly one worker existed, and silently broken the moment a second
one started answering requests, because the second worker would look up a session it had never
heard of and tell the user their sign-in had expired.

So the sessions live here instead, in a service pinned to one replica, and the API talks to it over
HTTP. The API can now run as many workers as it likes. This is the same shape as any other
single-owner resource: one holder, many callers.

Two properties this buys beyond correctness:

- **A slow sign-in cannot starve the API.** Reading a pty blocks a thread for up to a minute; here
  that thread is this service's, not the one serving everybody's dashboards.
- **The Claude CLI is installed in one image**, not in the image that also serves the SPA.

**Not exposed to the internet.** It has no authentication of its own and takes a user id as an
argument, so it is reachable only on the compose network. The API is what authenticates the caller
and it passes the id of the user it has already authenticated.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from libs import claudeauth
from libs.obs import audit, configure

logger = logging.getLogger(__name__)
configure("oauthbroker")

app = FastAPI(title="oauth broker", docs_url=None, redoc_url=None)


class StartBody(BaseModel):
    user_id: _uuid.UUID


class FinishBody(BaseModel):
    user_id: _uuid.UUID
    session_id: str = Field(min_length=8, max_length=128)
    code: str = Field(min_length=4, max_length=512)


class CancelBody(BaseModel):
    session_id: str = Field(min_length=8, max_length=128)


@app.get("/health")
def health() -> dict:
    """Whether a sign-in could actually start. **Reports the CLI, because its absence is the one
    failure that looks like nothing until a user clicks the button.**"""
    from shutil import which  # noqa: PLC0415

    found = which(claudeauth.CLI)
    return {"status": "ok" if found else "degraded",
            "cli": found or "missing",
            "live_sessions": len(claudeauth._SESSIONS)}


@app.post("/start")
def start(body: StartBody) -> dict:
    try:
        session_id, url = claudeauth.start(body.user_id)
    except claudeauth.OAuthError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    audit(logger, "oauth.started", user=body.user_id)
    return {"session_id": session_id, "url": url}


@app.post("/finish")
def finish(body: FinishBody) -> dict:
    """Returns the token. **The only response in the platform that carries a credential**, and it
    crosses the compose network to the API, which stores it and drops it."""
    try:
        token = claudeauth.finish(body.session_id, body.user_id, body.code)
    except claudeauth.OAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    audit(logger, "oauth.finished", user=body.user_id)
    return {"token": token}


@app.post("/cancel")
def cancel(body: CancelBody) -> dict:
    claudeauth.cancel(body.session_id)
    return {"status": "cancelled"}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    # One worker, and here that is a correctness requirement rather than a sizing choice: a second
    # worker would own a different set of sessions and answer for sessions it does not have.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8100")), workers=1)
