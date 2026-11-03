"""Blast-radius gate (PLAN §14.6): an unavailable embedder must raise, never return a garbage vector."""
from __future__ import annotations

import socket

import httpx
import pytest
from examples.client import EmbedderClient, EmbedderError


def _dead_url() -> str:
    """A port nothing is listening on (bound then released → connection refused, not a hang)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


@pytest.fixture()
def dead_client():
    c = EmbedderClient(_dead_url(), timeout=2.0)
    yield c
    c.close()


def test_health_raises_when_embedder_down(dead_client) -> None:
    with pytest.raises(httpx.HTTPError):
        dead_client.health()


def test_assert_compatible_refuses_when_embedder_down(dead_client) -> None:
    """Startup guard must refuse, not shrug — an unreachable embedder is not a compatible one."""
    with pytest.raises((EmbedderError, httpx.HTTPError)):
        dead_client.assert_compatible("BAAI/bge-base-en-v1.5", 768)


def test_embed_raises_rather_than_returning_empty(dead_client) -> None:
    """The failure must surface as an exception. A silent [] or zeros would be scored as if real."""
    with pytest.raises(httpx.HTTPError):
        dead_client.embed_score(["senior backend engineer, Go, remote EU"])
