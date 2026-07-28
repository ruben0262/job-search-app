"""The API service. FastAPI, one transaction per request.

    uvicorn services.api.app:app

Reached through a Cloudflare Tunnel (D7), so the VPS needs no open inbound port. That is also why
the cookies are `Secure` unconditionally: every real request arrives over HTTPS, and a
development-only exception is how the production path ends up untested.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from libs.obs import configure
from libs.secrets import install
from services.api.account_routes import router as account_router
from services.api.admin_routes import router as admin_router
from services.api.auth_routes import router as auth_router
from services.api.credential_routes import router as credential_router
from services.api.dashboard_routes import router as dashboard_router
from services.api.document_routes import router as document_router
from services.api.onboarding_routes import router as onboarding_router
from services.api.process_routes import router as process_router
from services.api.telegram_routes import router as telegram_router

configure("api")
# Before any route can hold a plaintext credential. Attaches to the root logger and its handlers,
# which is what covers records propagated from every library in the process.
install()

app = FastAPI(title="job-search platform", docs_url=None, redoc_url=None)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(credential_router)
app.include_router(document_router)
app.include_router(onboarding_router)
app.include_router(process_router)
app.include_router(dashboard_router)
app.include_router(telegram_router)


@app.get("/health")
def health() -> dict:
    """Liveness only. Deliberately does not touch the database.

    A health check that fails when Postgres is briefly unreachable causes the orchestrator to
    restart a process that was working fine, which is how a blip becomes an outage.
    """
    return {"status": "ok"}


# **The SPA is served by this process**, from `/app/web` in the image.
#
# `PLATFORM_ARCHITECTURE.md` D7 put the frontend on Cloudflare Pages, and that is still possible —
# but it needs a second deploy, a second origin, and a CORS policy that is untested until the day it
# is wrong. D7's actual requirement was **no open inbound port**, and the tunnel already delivers
# that. One origin, one deploy, one thing to be up: for a 69 kB bundle behind an invite-only beta,
# that trade is the right way round.
#
# Mounted **last**, so every API route above wins. `html=True` serves `index.html` at `/`, which is
# all a hash-routed app needs — there are no server-side paths to fall back for.
_WEB = Path(os.environ.get("WEB_DIR", "/app/web"))
if _WEB.is_dir():
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
else:
    # Never silent: an API answering 404 at `/` looks like a broken site rather than a missing
    # build, and that is exactly the hour somebody wastes.
    logging.getLogger(__name__).warning(
        "no web build at %s — API routes work, the site will 404 at /", _WEB)
