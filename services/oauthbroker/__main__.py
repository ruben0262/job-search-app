"""The one process that owns Claude sign-in sessions."""
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
    """Whether a sign-in could actually start."""
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
    """Returns the token."""
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

    # One worker, a correctness requirement: a second would answer for sessions it does not have.
    uvicorn.run(app, host="0.0.0.0",  # noqa: S104
                port=int(os.environ.get("PORT", "8100")), workers=1)
