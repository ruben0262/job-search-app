"""Optional bearer-token auth.

OFF by default (EMBEDDER_AUTH_TOKEN unset) → shared-Docker-network-only mode, where the network
boundary is the security boundary (PLAN network decision). Set the token BEFORE publishing any
host port / exposing remotely — an un-authed embedding endpoint is a CPU-DoS magnet.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import CONFIG


async def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not CONFIG.auth_token:
        return  # network-only mode: no token configured, nothing to check
    expected = f"Bearer {CONFIG.auth_token}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="missing or invalid bearer token")
