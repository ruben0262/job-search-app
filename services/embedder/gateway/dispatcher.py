"""Two-lane dispatch in front of TEI (PLAN §4b/§4c)."""
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
        """Split into chunks, dispatch them on `lane`, reassemble in the caller's order."""
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
