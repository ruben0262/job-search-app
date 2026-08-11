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


# ── a dependency must live in the image that uses it (2026-08-11) ─────────────────────────────

def test_the_image_that_detects_a_language_declares_the_library_that_detects_it():
    """**The language filter was inert platform-wide and nothing said so.**

    `langdetect` was declared only in the notifier's `requirements.txt`. The code that calls
    `detect_language` is `libs/corpus/repository` — `set_description` and `fill_missing_languages` —
    which runs in the **harvester**, and that image never carried it. The `ImportError` was caught by
    the same blind `except` as an unreadable posting and returned `None`; `is_allowed` keeps `None`
    on purpose; so every posting passed every account's language filter.

    Measured on the live platform: one account went from 608 postings with a detected language on
    2026-08-06 to 1 on 2026-08-10, while its Your Details languages setting went on being saved and
    displayed. Nothing raised and nothing was logged.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    # Where the detection actually happens, asserted rather than assumed — if this moves, the test
    # should be updated with it rather than quietly guarding the wrong service.
    repository = (root / "libs" / "corpus" / "repository.py").read_text(encoding="utf-8")
    assert "detect_language" in repository, (
        "detection has moved out of libs/corpus/repository; point this test at its new caller")

    harvester = (root / "services" / "harvester" / "requirements.txt").read_text(encoding="utf-8")
    assert "langdetect" in harvester, (
        "the harvester writes every posting's language and must carry langdetect, or the column is "
        "NULL forever and every account's language filter admits everything")


def test_no_service_declares_a_library_it_never_reaches():
    """The other half: a dependency left behind after the code moved is how the first one happened.

    Not a general rule — proving reachability is beyond a text scan — but pinned for `langdetect`,
    which is the one that has already drifted once.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for service in ("notifier", "scorer", "api"):
        requirements = root / "services" / service / "requirements.txt"
        if not requirements.exists():
            continue
        declares = "langdetect>=" in requirements.read_text(encoding="utf-8")
        reaches = any(
            "langfilter" in path.read_text(encoding="utf-8")
            for path in (root / "services" / service).rglob("*.py"))
        assert not (declares and not reaches), (
            f"services/{service} declares langdetect and never reaches libs/corpus/langfilter — "
            "that is the drift that left the harvester without it")
