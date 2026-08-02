"""Every service entry point imports.

**Why this exists.** `services/oauthbroker` shipped importing `setup_logging` from `libs.obs`, a
name that has never existed there. Nothing caught it: the unit tests import the service's helpers
and not its `__main__`, the image built happily, and the deploy verification confirmed the *files*
in the container matched the host, which they did. It failed on the one thing nobody was checking,
which is whether the process starts.

An import is not a smoke test, but it catches the whole class this belongs to: a renamed helper, a
dependency missing from a service's requirements, a module moved between libs. All of them are
otherwise found by a container restart-looping in production.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest

import services

_ROOT = pathlib.Path(services.__path__[0])

# Whatever a container actually runs. Most services are `python -m services.<name>`, so their entry
# point is `__main__`; the API is served by uvicorn from `app:app` and has no `__main__` at all.
# Discovered rather than listed, so a new service is covered the day it is added.
ENTRYPOINTS = [
    *sorted(f"services.{path.parent.name}.__main__" for path in _ROOT.glob("*/__main__.py")),
    "services.api.app",
]


def test_there_are_services_to_check():
    """Guards the guard: a discovery bug here would make every assertion below vacuous."""
    assert len(ENTRYPOINTS) >= 5, ENTRYPOINTS


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_entrypoint_imports(name):
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        # A third-party package absent from *this* environment is not the failure being hunted;
        # a name missing from our own code is.
        if not str(exc).startswith(("No module named 'libs", "No module named 'services")):
            pytest.skip(f"optional dependency not installed here: {exc}")
        raise
