"""Every deployable entrypoint imports.

Trivial-looking, and it exists because the absence of it cost a full CI cycle: `process_routes.py`
imported a name `libs/queue/__init__.py` did not export, which broke **every** API route at import
time. Nothing caught it locally, because the tests that import the app are the DB-backed ones and
they skip without Postgres — so the one failure that takes down the whole service was invisible on
the machine where the code was written.

This runs without a database, without credentials, and without the network. It asserts one thing:
the module a systemd unit or `uvicorn` invocation names can be loaded at all.
"""
from __future__ import annotations

import importlib

import pytest

# Every module named by a deploy artifact — a systemd unit, a compose service, or the uvicorn
# target. Add a row here when a service gains an entrypoint, not after it fails to start.
ENTRYPOINTS = [
    "services.api.app",
    "services.capture.__main__",
    "services.harvester.__main__",
    "services.labeller.__main__",
    "services.notifier.__main__",
    "services.trainer.__main__",
]


@pytest.mark.parametrize("module", ENTRYPOINTS)
def test_the_entrypoint_imports(module):
    importlib.import_module(module)


def test_the_api_registers_its_routers():
    """An unregistered router is a 404 on every one of its paths, which looks like a client bug.

    Checked through the OpenAPI schema rather than by counting `app.routes`, because the routers are
    included lazily and the schema is what a client actually sees.
    """
    pytest.importorskip("fastapi")
    import os

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as http:
        paths = http.get("/openapi.json").json()["paths"]

    for prefix in ("/account", "/admin", "/auth", "/credentials", "/dashboard",
                   "/documents", "/feed", "/legal", "/onboarding", "/process",
                   "/telegram"):
        assert any(path.startswith(prefix) for path in paths), f"{prefix} is not registered"
