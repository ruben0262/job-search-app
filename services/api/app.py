"""The API service."""
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
from services.api.notification_routes import router as notification_router
from services.api.onboarding_routes import router as onboarding_router
from services.api.setup_routes import router as setup_router
from services.api.status_routes import router as status_router
from services.api.telegram_routes import router as telegram_router

configure("api")
# Before any route can hold a plaintext credential.
install()

app = FastAPI(title="job-search platform", docs_url=None, redoc_url=None)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(admin_router)
app.include_router(credential_router)
app.include_router(document_router)
app.include_router(onboarding_router)
app.include_router(setup_router)
app.include_router(dashboard_router)
app.include_router(notification_router)
app.include_router(status_router)
app.include_router(telegram_router)


@app.get("/health")
def health() -> dict:
    """Liveness only."""
    return {"status": "ok"}


# The SPA is served by this process, from `/app/web` in the image.
_WEB = Path(os.environ.get("WEB_DIR", "/app/web"))
if _WEB.is_dir():
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
else:
    # Never silent: an API answering 404 at `/` looks like a broken site, not a missing build.
    logging.getLogger(__name__).warning(
        "no web build at %s — API routes work, the site will 404 at /", _WEB)
