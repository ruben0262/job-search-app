"""The API service."""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from libs import assistant
from libs.db import ensure_schema
from libs.notify import site_url
from libs.obs import configure
from libs.secrets import install
from services.api.account_routes import router as account_router
from services.api.admin_routes import router as admin_router
from services.api.auth_routes import router as auth_router
from services.api.contact_routes import router as contact_router
from services.api.credential_routes import router as credential_router
from services.api.dashboard_routes import router as dashboard_router
from services.api.document_routes import router as document_router
from services.api.link_routes import router as link_router
from services.api.notification_routes import router as notification_router
from services.api.onboarding_routes import router as onboarding_router
from services.api.push_routes import router as push_router
from services.api.saved_routes import router as saved_router
from services.api.suggestion_routes import router as suggestion_router
from services.api.support_routes import router as support_router
from services.api.telegram_routes import router as telegram_router

configure("api")
# Before any route can hold a plaintext credential.
install()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """The schema first, so nothing is served before its unique rules exist; then the help-chat
    replies the last stop cut off are written again — off the start-up path."""
    ensure_schema()
    threading.Thread(target=assistant.resume, daemon=True, name="support-resume").start()
    yield


app = FastAPI(title="job-search platform", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(contact_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(credential_router)
app.include_router(document_router)
app.include_router(onboarding_router)
app.include_router(push_router)
app.include_router(saved_router)
app.include_router(dashboard_router)
app.include_router(link_router)
app.include_router(notification_router)
app.include_router(suggestion_router)
app.include_router(support_router)
app.include_router(telegram_router)


@app.get("/health")
def health() -> dict:
    """Liveness, and the address this site's emails link to: `deploy/reach.sh` asks for it through
    that address, so a link that would land on nothing, or on another server, fails the deploy."""
    return {"status": "ok", "public_url": site_url()}


# The SPA is served by this process, from `/app/web` in the image.
_WEB = Path(os.environ.get("WEB_DIR", "/app/web"))
if _WEB.is_dir():
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
else:
    # Never silent: an API answering 404 at `/` looks like a broken site, not a missing build.
    logging.getLogger(__name__).warning(
        "no web build at %s — API routes work, the site will 404 at /", _WEB)
