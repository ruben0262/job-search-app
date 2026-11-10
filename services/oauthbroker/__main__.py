"""The one process that holds AI sign-ins (`libs.signin`): Claude, ChatGPT (Codex) and Google (Gemini).

Its own process because a sign-in is a live terminal session that must stay in the memory of whoever
started it, across the person's trip to the provider's page and back: the API scales to several
workers, this runs one. It hands the finished login to the API, which seals and stores it.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from libs import signin
from libs.llm import cli
from libs.obs import audit, configure

logger = logging.getLogger(__name__)
configure("oauthbroker")

app = FastAPI(title="oauth broker", docs_url=None, redoc_url=None)

Kind = Literal["claude_login", "codex_login", "gemini_login"]


class StartBody(BaseModel):
    user_id: _uuid.UUID
    kind: Kind


class FinishBody(BaseModel):
    user_id: _uuid.UUID
    session_id: str = Field(min_length=8, max_length=128)
    # The code Claude's or Google's page shows; none for Codex, whose code is entered on OpenAI's page.
    code: str | None = Field(default=None, min_length=4, max_length=512)


class CancelBody(BaseModel):
    session_id: str = Field(min_length=8, max_length=128)


@app.get("/health")
def health() -> dict:
    """Whether each sign-in could actually start: its tool is installed here."""
    from shutil import which  # noqa: PLC0415

    found = {kind: which(tool.path()) for kind, tool in cli.TOOLS.items()}
    return {"status": "ok" if all(found.values()) else "degraded",
            "tools": {kind: path or "missing" for kind, path in found.items()},
            "live_sessions": signin.live()}


@app.post("/start")
def start(body: StartBody) -> dict:
    try:
        started = signin.start(body.user_id, body.kind)
    except signin.SignInError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    audit(logger, "signin.started", user=body.user_id, kind=body.kind)
    return {"session_id": started.session_id, "url": started.url, "code": started.code}


@app.post("/finish")
def finish(body: FinishBody) -> dict:
    """The account's login files and label once signed in, or `waiting` while Codex still is."""
    try:
        done = signin.finish(body.session_id, body.user_id, body.code)
    except signin.SignInError as exc:
        # Gone: the sign-in ended, and the page offers a new link. Otherwise the same one takes a code.
        ended = not signin.alive(body.session_id)
        raise HTTPException(status.HTTP_410_GONE if ended else status.HTTP_400_BAD_REQUEST,
                            detail=str(exc)) from exc
    if done is None:
        return {"status": "waiting"}
    audit(logger, "signin.finished", user=body.user_id)
    return {"status": "done", "secret": done.secret, "label": done.label}


@app.post("/cancel")
def cancel(body: CancelBody) -> dict:
    signin.cancel(body.session_id)
    return {"status": "cancelled"}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    # One worker, a correctness requirement: a second would answer for sessions it does not have.
    uvicorn.run(app, host="0.0.0.0",  # noqa: S104
                port=int(os.environ.get("PORT", "8100")), workers=1)
