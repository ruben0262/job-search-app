"""Async HTTP client for the TEI backend."""
from __future__ import annotations

import httpx

from .config import CONFIG


class TEIClient:
    def __init__(self, base_url: str = CONFIG.tei_url, timeout: float = 120.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One forward pass over `texts`. TEI returns a vector per input, in order."""
        resp = await self._client.post(
            "/embed",
            json={"inputs": texts, "normalize": CONFIG.normalize, "truncate": True},
        )
        resp.raise_for_status()
        return resp.json()

    async def info(self) -> dict:
        resp = await self._client.get("/info")
        resp.raise_for_status()
        return resp.json()

    async def healthy(self) -> bool:
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
