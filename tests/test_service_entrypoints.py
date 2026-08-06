"""Every service entry point imports."""
from __future__ import annotations

import importlib
import pathlib

import pytest

import services

_ROOT = pathlib.Path(services.__path__[0])

# Whatever a container actually runs.
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
        # A third-party package absent from *this* environment is not the failure being hunted.
        if not str(exc).startswith(("No module named 'libs", "No module named 'services")):
            pytest.skip(f"optional dependency not installed here: {exc}")
        raise
