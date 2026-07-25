"""FastAPI gateway for the shared embedder.

Endpoints:
    GET  /health       -> {ok, model, dim, normalize, version, tei_ok, verified, ...}
    POST /embed        -> fast lane   (on-arrival scoring; a user is waiting)
    POST /embed/bulk   -> bulk lane   (training corpus; throughput over latency)

Both return {vectors, model, dim, version}. Callers assert model+dim+version against their
trained artifact and refuse to score on a mismatch (PLAN §13).

`ok` is true only once the configured (model, dim) has been confirmed against the LIVE backend
(see verify.py) — reporting the `.env`'s claims unchecked would let a config drift sail through
every caller's assert. Until then, and forever after a mismatch, the embed endpoints 503 so
callers fail closed rather than scoring against the wrong vector space (PLAN §13/§14.6).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from .auth import require_auth
from .config import CONFIG
from .dispatcher import Dispatcher, QueueFull
from .tei_client import TEIClient
from .verify import Verifier

_tei: TEIClient | None = None
_dispatcher: Dispatcher | None = None
_verifier: Verifier | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tei, _dispatcher, _verifier
    _tei = TEIClient()
    _verifier = Verifier(_tei)
    _dispatcher = Dispatcher(_tei, _verifier)
    _dispatcher.start()
    _verifier.start()
    yield
    if _verifier:
        await _verifier.stop()
    if _dispatcher:
        await _dispatcher.stop()
    if _tei:
        await _tei.aclose()


app = FastAPI(title="job-search-embedder", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1)


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    model: str
    dim: int
    version: str


def _check_size(texts: list[str]) -> None:
    if len(texts) > CONFIG.max_texts:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{len(texts)} texts exceeds EMBEDDER_MAX_TEXTS={CONFIG.max_texts}")


def _check_ready() -> None:
    """Fail closed until the backend's vector space is confirmed (PLAN §13/§14.6)."""
    if _dispatcher is None or _verifier is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="gateway not started")
    if not _verifier.verified:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"embedder not serving: {_verifier.detail}")


def _response(vectors: list[list[float]]) -> EmbedResponse:
    return EmbedResponse(vectors=vectors, model=CONFIG.model, dim=CONFIG.dim, version=CONFIG.version)


async def _dispatch(coro) -> EmbedResponse:
    try:
        return _response(await coro)
    except QueueFull as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(exc)) from exc


@app.get("/health")
async def health() -> dict:
    tei_ok = await _tei.healthy() if _tei else False
    verify = _verifier.info() if _verifier else {"verified": False, "verify_state": "not started",
                                                 "verify_detail": "gateway not started"}
    return {"ok": tei_ok and verify["verified"], "tei_ok": tei_ok,
            "queue_depth": _dispatcher.depth if _dispatcher else -1,
            **verify, **CONFIG.info()}


@app.post("/embed", response_model=EmbedResponse, dependencies=[Depends(require_auth)])
async def embed(req: EmbedRequest) -> EmbedResponse:
    """Fast lane — jumps ahead of queued bulk work."""
    _check_ready()
    _check_size(req.texts)
    return await _dispatch(_dispatcher.submit_score(req.texts))


@app.post("/embed/bulk", response_model=EmbedResponse, dependencies=[Depends(require_auth)])
async def embed_bulk(req: EmbedRequest) -> EmbedResponse:
    """Bulk lane — chunked so the fast lane can interleave; yields to /embed under load."""
    _check_ready()
    _check_size(req.texts)
    return await _dispatch(_dispatcher.submit_bulk(req.texts))
