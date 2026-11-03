"""Server-side enforcement of the version contract (PLAN §13)."""
from __future__ import annotations

import asyncio
import logging

from .config import CONFIG
from .tei_client import TEIClient

log = logging.getLogger("embedder.verify")

PENDING = "pending"          # still probing (TEI may still be downloading weights)
OK = "ok"                    # model + dim confirmed against the live backend
MISMATCH = "mismatch"        # terminal: config lies about the backend — never serve as healthy
UNREACHABLE = "unreachable"  # backend down; keep retrying

_PROBE = "embedder version probe"


class Verifier:
    """Background probe holding the backend-verified state behind /health."""

    def __init__(self, tei: TEIClient, interval: float = 5.0) -> None:
        self._tei = tei
        self._interval = interval
        self._task: asyncio.Task | None = None
        self.state: str = PENDING
        self.detail: str = "not probed yet"
        # What TEI will actually accept in one call.
        self.backend_max_batch: int | None = None

    @property
    def verified(self) -> bool:
        return self.state == OK

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await self._probe_once()
            if self.state in (OK, MISMATCH):
                return  # both terminal: nothing about a loaded model changes without a restart
            await asyncio.sleep(self._interval)

    async def _probe_once(self) -> None:
        try:
            info = await self._tei.info()
            vectors = await self._tei.embed([_PROBE])
        except Exception as exc:
            self.state, self.detail = UNREACHABLE, f"backend not ready: {exc}"
            return

        served_model = str(info.get("model_id", "")).strip()
        served_dim = len(vectors[0]) if vectors else -1

        # Capacity, not correctness: a batch over the backend's limit is a 413, so this clamps.
        limit = info.get("max_client_batch_size")
        if isinstance(limit, int) and limit > 0:
            self.backend_max_batch = limit
            if limit < CONFIG.tei_batch:
                log.warning("backend max_client_batch_size=%d is below TEI_MAX_CLIENT_BATCH=%d — "
                            "clamping outgoing batches to %d", limit, CONFIG.tei_batch, limit)
        problems = []
        if served_model != CONFIG.model:
            problems.append(f"model: configured {CONFIG.model!r}, backend serves {served_model!r}")
        if served_dim != CONFIG.dim:
            problems.append(f"dim: configured {CONFIG.dim}, backend returns {served_dim}")

        if problems:
            self.state = MISMATCH
            self.detail = ("REFUSING TO SERVE — " + "; ".join(problems) +
                           ". Fix EMBEDDER_MODEL/EMBEDDER_DIM or the tei --model-id so they agree; "
                           "a mismatch here would make every caller's assert_compatible pass "
                           "against the wrong vector space (PLAN §13).")
            log.error(self.detail)
            return

        self.state = OK
        self.detail = f"backend confirmed: model={served_model} dim={served_dim}"
        log.info(self.detail)

    def effective_batch(self, want: int) -> int:
        """`want` texts per TEI call, lowered to whatever the backend actually accepts."""
        if self.backend_max_batch:
            return max(1, min(want, self.backend_max_batch))
        return max(1, want)

    def info(self) -> dict:
        return {"verified": self.verified, "verify_state": self.state, "verify_detail": self.detail,
                "backend_max_batch": self.backend_max_batch}
