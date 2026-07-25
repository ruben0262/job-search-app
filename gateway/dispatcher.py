"""Two-lane priority dispatch in front of TEI (PLAN §4b).

The problem: on-arrival scoring (a user waiting) and bulk training embeds share ONE TEI
backend. A big training flood must not make a score request wait. Solution: a priority queue
where score items (priority 0) are pulled before bulk items (priority 1), and bulk requests
are split into bounded chunks so score items can interleave *between* a training request's
chunks instead of queueing behind the whole thing.

TEI still does the real token-batching internally; this layer only controls ORDER and caps how
much bulk work is in flight, so the fast lane always has a way through.

The queue is BOUNDED (`EMBEDDER_MAX_QUEUE`, PLAN §4c "bound the queue"): past that depth the
gateway sheds load with a 503 rather than growing memory without limit while every caller times
out anyway. Backpressure is the honest signal — an unbounded queue just converts a throughput
problem into an OOM.

TODO(tune): the chunk size, worker count, queue bound, and (later) an explicit max-queue-delay
window are empirical — set from load tests on the real box (PLAN §12/§14.8), not guessed here.
"""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field

from .config import CONFIG
from .tei_client import TEIClient

SCORE = 0   # high-priority lane
BULK = 1    # bulk/training lane


class QueueFull(RuntimeError):
    """Dispatch queue at its bound — shed load instead of growing memory (PLAN §4c)."""


@dataclass(order=True)
class _Item:
    priority: int
    seq: int
    texts: list[str] = field(compare=False)
    future: asyncio.Future = field(compare=False)


class Dispatcher:
    def __init__(self, tei: TEIClient) -> None:
        self._tei = tei
        self._queue: asyncio.PriorityQueue[_Item] = asyncio.PriorityQueue(
            maxsize=max(1, CONFIG.max_queue))
        self._seq = itertools.count()        # tie-breaker → FIFO within a priority level
        self._workers: list[asyncio.Task] = []

    def start(self) -> None:
        self._workers = [asyncio.create_task(self._worker())
                         for _ in range(max(1, CONFIG.tei_concurrency))]

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    async def _enqueue(self, priority: int, texts: list[str]) -> list[list[float]]:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_Item(priority, next(self._seq), texts, fut))
        except asyncio.QueueFull as exc:
            raise QueueFull(
                f"dispatch queue full ({CONFIG.max_queue} items) — retry with backoff") from exc
        return await fut

    async def submit_score(self, texts: list[str]) -> list[list[float]]:
        """Fast lane — forwarded ahead of any queued bulk work."""
        if not texts:
            return []
        return await self._enqueue(SCORE, texts)

    async def submit_bulk(self, texts: list[str]) -> list[list[float]]:
        """Bulk lane — split into chunks so score items can slip in between them; reassembled
        in original order."""
        if not texts:
            return []
        chunks = [texts[i:i + CONFIG.bulk_chunk] for i in range(0, len(texts), CONFIG.bulk_chunk)]
        results = await asyncio.gather(*(self._enqueue(BULK, c) for c in chunks))
        return [vec for chunk_vecs in results for vec in chunk_vecs]

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                vectors = await self._tei.embed(item.texts)
                if not item.future.done():
                    item.future.set_result(vectors)
            except Exception as exc:   # surface to the awaiting request, keep the worker alive
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                self._queue.task_done()
