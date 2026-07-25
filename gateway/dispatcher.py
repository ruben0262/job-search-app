"""Two-lane dispatch in front of TEI (PLAN §4b/§4c).

The problem: on-arrival scoring (a user waiting) and bulk training embeds share ONE TEI backend.
A training flood must not make a score request wait.

A single priority queue is NOT enough, and the first VPS run proved it — score latency went from
0.08s idle to 8-15s under a bulk flood. Priority decides who is picked up NEXT; it cannot recall a
worker already blocked inside a multi-second TEI call. With every worker busy on bulk chunks, a
score item sits at the front of the queue and waits anyway (classic head-of-line blocking).

So the lanes get SEPARATE worker pools:
  - score workers serve /embed only, and are therefore always free to dispatch immediately,
  - bulk workers serve /embed/bulk, deliberately few (default 1) so a flood cannot fill TEI's own
    internal queue and push score requests behind a wall of queued batches.

Chunk size is the other half. TEI's CPU backend caps its internal batch at 8, so forwarding more
than that per call buys no inference speed — it only lengthens the window during which a worker,
and TEI's queue head, is unavailable. Hence a small bulk chunk by default.

Both queues are BOUNDED (`EMBEDDER_MAX_QUEUE`): past that depth the gateway sheds load with a 503
rather than growing memory while every caller times out anyway. An unbounded queue just converts a
throughput problem into an OOM.

TODO(tune): chunk size, pool sizes, and the queue bound are empirical — set them from
`scripts/load_rss.py` + `tests/test_priority_lane.py` on the real box (PLAN §12/§14.8).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .config import CONFIG
from .tei_client import TEIClient

SCORE = "score"
BULK = "bulk"


class QueueFull(RuntimeError):
    """A lane's queue is at its bound — shed load instead of growing memory (PLAN §4c)."""


@dataclass
class _Item:
    texts: list[str]
    future: asyncio.Future = field(compare=False)


class Dispatcher:
    def __init__(self, tei: TEIClient, verifier=None) -> None:
        self._tei = tei
        self._verifier = verifier   # supplies the backend's real per-call batch limit (verify.py)
        bound = max(1, CONFIG.max_queue)
        self._queues: dict[str, asyncio.Queue[_Item]] = {
            SCORE: asyncio.Queue(maxsize=bound),
            BULK: asyncio.Queue(maxsize=bound),
        }
        self._workers: list[asyncio.Task] = []

    def start(self) -> None:
        pools = ((SCORE, max(1, CONFIG.score_workers)), (BULK, max(1, CONFIG.bulk_workers)))
        self._workers = [asyncio.create_task(self._worker(lane))
                         for lane, count in pools for _ in range(count)]

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    @property
    def depth(self) -> int:
        return sum(q.qsize() for q in self._queues.values())

    def depths(self) -> dict[str, int]:
        return {lane: q.qsize() for lane, q in self._queues.items()}

    async def _enqueue(self, lane: str, texts: list[str]) -> list[list[float]]:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        try:
            self._queues[lane].put_nowait(_Item(texts, fut))
        except asyncio.QueueFull as exc:
            raise QueueFull(
                f"{lane} queue full ({CONFIG.max_queue} chunks) — retry with backoff") from exc
        return await fut

    async def _submit(self, lane: str, texts: list[str], chunk: int) -> list[list[float]]:
        """Split into chunks, dispatch them on `lane`, reassemble in the caller's order.

        Chunking is not optional on either lane: TEI refuses any single call over its
        --max-client-batch-size, so an unchunked request from a caller embedding a page of postings
        would 413 rather than embed.
        """
        if not texts:
            return []
        if self._verifier is not None:
            chunk = self._verifier.effective_batch(chunk)
        parts = [texts[i:i + chunk] for i in range(0, len(texts), chunk)]
        results = await asyncio.gather(*(self._enqueue(lane, p) for p in parts))
        return [vec for part_vecs in results for vec in part_vecs]

    async def submit_score(self, texts: list[str]) -> list[list[float]]:
        """Fast lane — its own workers, so it never waits on an in-flight bulk call."""
        return await self._submit(SCORE, texts, CONFIG.chunk_score)

    async def submit_bulk(self, texts: list[str]) -> list[list[float]]:
        """Bulk lane — few workers, fine chunks, so it yields the backend to the fast lane."""
        return await self._submit(BULK, texts, CONFIG.chunk_bulk)

    async def _worker(self, lane: str) -> None:
        queue = self._queues[lane]
        while True:
            item = await queue.get()
            try:
                vectors = await self._tei.embed(item.texts)
                if not item.future.done():
                    item.future.set_result(vectors)
            except Exception as exc:   # surface to the awaiting request, keep the worker alive
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                queue.task_done()
