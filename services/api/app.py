"""The API service. FastAPI, one transaction per request.

    uvicorn services.api.app:app

Reached through a Cloudflare Tunnel (D7), so the VPS needs no open inbound port. That is also why
the cookies are `Secure` unconditionally: every real request arrives over HTTPS, and a
development-only exception is how the production path ends up untested.
"""
from __future__ import annotations

from fastapi import FastAPI

from libs.obs import configure
from libs.secrets import install
from services.api.auth_routes import router as auth_router
from services.api.credential_routes import router as credential_router
from services.api.document_routes import router as document_router
from services.api.onboarding_routes import router as onboarding_router

configure("api")
# Before any route can hold a plaintext credential. Attaches to the root logger and its handlers,
# which is what covers records propagated from every library in the process.
install()

app = FastAPI(title="job-search platform", docs_url=None, redoc_url=None)
app.include_router(auth_router)
app.include_router(credential_router)
app.include_router(document_router)
app.include_router(onboarding_router)


@app.get("/health")
def health() -> dict:
    """Liveness only. Deliberately does not touch the database.

    A health check that fails when Postgres is briefly unreachable causes the orchestrator to
    restart a process that was working fine, which is how a blip becomes an outage.
    """
    return {"status": "ok"}
