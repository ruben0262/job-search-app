"""Version/dim assertion gate (PLAN §14.2 + §13).

Two halves of the same contract:
  - CALLER side: `assert_compatible` must refuse when its artifact's (model, dim) differs from
    what the service reports. A classifier bound to another vector space must never score.
  - SERVICE side: `/health.ok` must be backed by a probe of the LIVE backend (`verified`), not by
    the gateway's own `.env`. Otherwise a config drift satisfies every caller's assert while the
    vectors are wrong — the exact silent failure §13 exists to prevent.

Integration test — needs a running stack (EMBEDDER_URL).
"""
from __future__ import annotations

import os

import pytest

from examples.client import EmbedderClient, IncompatibleEmbedder

URL = os.environ.get("EMBEDDER_URL")
pytestmark = pytest.mark.skipif(not URL, reason="EMBEDDER_URL not set (no running stack)")


@pytest.fixture()
def client():
    c = EmbedderClient(URL, os.environ.get("EMBEDDER_TOKEN") or None, timeout=30.0)
    yield c
    c.close()


def test_health_is_backend_verified(client) -> None:
    """ok must mean 'confirmed against the live backend', not 'the env file says so'."""
    h = client.health()
    assert h.get("verified") is True, (
        f"/health not backend-verified: state={h.get('verify_state')} {h.get('verify_detail')}")
    assert h["ok"] is True


def test_matching_artifact_passes(client) -> None:
    h = client.health()
    client.assert_compatible(h["model"], h["dim"])   # must not raise


def test_wrong_model_is_refused(client) -> None:
    h = client.health()
    other = ("BAAI/bge-base-en-v1.5" if h["model"] != "BAAI/bge-base-en-v1.5"
             else "BAAI/bge-small-en-v1.5")
    with pytest.raises(IncompatibleEmbedder):
        client.assert_compatible(other, h["dim"])


def test_wrong_dim_is_refused(client) -> None:
    h = client.health()
    with pytest.raises(IncompatibleEmbedder):
        client.assert_compatible(h["model"], h["dim"] + 1)


def test_reported_dim_matches_actual_vector(client) -> None:
    """The number in /health must equal the width of a real vector — the classifier's input width."""
    h = client.health()
    vec = client.embed_score(["backend engineer, Go and Postgres, fully remote EU"])[0]
    assert len(vec) == h["dim"], f"/health says dim={h['dim']} but a vector has {len(vec)} floats"
