"""Server-side enforcement of the version contract (PLAN §13).

Callers assert their classifier's (model, dim) against `/health` before trusting a score. That
assert is only worth anything if `/health` reports what TEI *actually loaded* — not merely what
this container's `.env` claims. Without this check, an `EMBEDDER_MODEL`/`EMBEDDER_DIM` that drifts
from the model TEI was started with passes every caller's assert and every score is silently wrong,
which is precisely the failure §13 exists to prevent.

So at startup the gateway probes the backend and refuses to declare itself healthy until:
  - TEI's `/info.model_id` == `CONFIG.model`, and
  - a real embed of a probe string returns exactly `CONFIG.dim` floats.

A MISMATCH is terminal and loud: `/health.ok` stays false forever, so callers fail closed
(PLAN §14.6) instead of scoring against the wrong vector space. An unreachable backend is
transient (TEI downloads weights on first boot) and keeps retrying.
"""
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

    def info(self) -> dict:
        return {"verified": self.verified, "verify_state": self.state, "verify_detail": self.detail}
