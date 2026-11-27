"""Every deployable entrypoint finds what it imports in the image that runs it, and the API is put together
whole. That each one imports here is `test_service_entrypoints.py`."""
from __future__ import annotations

import ast
import functools
import re
import sys
from importlib.metadata import PackageNotFoundError, packages_distributions, requires
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Every module named by a deploy artifact (a compose service, or the uvicorn target), and the
# requirements its image installs (`deploy/docker/Dockerfile.*`; seed-ats installs the harvester's).
IMAGES = {
    "services.api.app": ["services/api/requirements.txt"],
    "services.docgen.__main__": ["services/docgen/requirements.txt"],
    "services.harvester.__main__": ["services/harvester/requirements.txt"],
    "services.harvester.describer": ["services/harvester/requirements.txt"],
    "services.notifier.__main__": ["services/notifier/requirements.txt"],
    "services.oauthbroker.__main__": ["services/oauthbroker/requirements.txt"],
}


def test_every_module_a_container_starts_is_one_checked_against_its_image():
    """The list above is kept by hand, and the describer, which compose starts on the harvester's image with
    an `entrypoint:` of its own, was not on it (found 2026-11-26): what it imports at start was checked
    against no image. So the list is held to what the deploy files start, a module run with `-m` or served
    by uvicorn, in a Dockerfile or in compose."""
    deploy = ROOT / "deploy"
    files = [deploy / "compose.yml", *sorted((deploy / "docker").glob("Dockerfile.*"))]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    named = {*re.findall(r'"python", "-m", "([\w.]+)"', text), *re.findall(r'"uvicorn", "([\w.]+):', text)}
    started = {name if _source(name).name != "__init__.py" else f"{name}.__main__" for name in named}
    assert len(started) >= 6, f"the premise: the deploy files' entrypoints are read ({started})"
    unlisted = started - set(IMAGES)
    assert not unlisted, f"started by a container and checked against no image: {unlisted}"


@pytest.fixture(scope="module")
def by_module() -> dict[str, list[str]]:
    """The installed package each importable name comes from. Read once: a fifth of a second every time."""
    return packages_distributions()


@pytest.mark.parametrize("module", IMAGES)
def test_everything_an_entrypoint_imports_at_start_is_installed_in_its_image(module, by_module):
    """Importing it passes here (`test_service_entrypoints.py`), where every service's packages are
    installed at once: the sign-in broker crashed on the server at start for want of httpx (2026-11-10),
    which `libs.llm`, newly reached through `libs.signin`, imports. So the imports are followed from the
    entrypoint through `libs` and `services`, as Python runs them at start (inside functions is not at
    start), and each package found must be one the image's requirements install, or a package one of them
    needs."""
    needed = _imported_at_start(module)
    installed = _installed_by(IMAGES[module])
    missing = {top: where for top, where in needed.items()
               if not {_name(each) for each in by_module.get(top, [top])} & installed}
    assert not missing, f"imported at start but not in {IMAGES[module]}: {missing}"


def _name(distribution: str) -> str:
    return re.sub(r"[-_.]+", "-", distribution).lower()


def _installed_by(files: list[str]) -> set[str]:
    """The packages the requirements name, and everything those need in turn."""
    wanted = [re.split(r"[\[<>=!~; ]", line.split("#")[0].strip())[0]
              for file in files for line in (ROOT / file).read_text().splitlines()
              if line.split("#")[0].strip() and not line.startswith("-")]
    installed: set[str] = set()
    while wanted:
        name = _name(wanted.pop())
        if name in installed:
            continue
        installed.add(name)
        try:
            needs = requires(name) or []
        except PackageNotFoundError:
            continue
        wanted += [re.split(r"[\[<>=!~; (]", need)[0] for need in needs if "extra ==" not in need]
    return installed


@functools.cache
def _source(module: str) -> Path | None:
    base = ROOT.joinpath(*module.split("."))
    return next((path for path in (base.with_suffix(".py"), base / "__init__.py") if path.is_file()), None)


@functools.cache
def _statements(module: str) -> list[ast.stmt]:
    """A module's own statements. Kept: the five entrypoints reach most of `libs` each."""
    return ast.parse(_source(module).read_text()).body


def _imported_at_start(entry: str) -> dict[str, str]:
    """Each package outside the standard library imported at start, and one of our modules importing it.
    An import a `try` guards against ImportError is optional, and one under TYPE_CHECKING never runs."""
    found: dict[str, str] = {}
    seen: set[str] = set()
    stack = [entry]

    def visit(body: list[ast.stmt], module: str, guarded: bool) -> None:
        for node in body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    package = module.split(".")
                    if _source(module) and _source(module).name != "__init__.py":
                        package = package[:-1]
                    parent = package[:len(package) - node.level + 1]
                    base = ".".join([*parent, base] if base else parent)
                names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            elif isinstance(node, ast.If) and "TYPE_CHECKING" not in ast.unparse(node.test):
                visit(node.body, module, guarded)
                visit(node.orelse, module, guarded)
            elif isinstance(node, ast.Try):
                caught = {ast.unparse(handler.type) for handler in node.handlers if handler.type}
                visit(node.body, module, guarded or bool(caught & {"ImportError", "ModuleNotFoundError"}))
                for block in (*[handler.body for handler in node.handlers], node.orelse, node.finalbody):
                    visit(block, module, guarded)
            elif isinstance(node, (ast.With, ast.ClassDef)):
                visit(node.body, module, guarded)
            for name in names:
                top = name.split(".")[0]
                if top in ("libs", "services"):
                    stack.append(name)
                elif top not in sys.stdlib_module_names and top != "__future__" and not guarded:
                    found.setdefault(top, module)

    while stack:
        parts = stack.pop().split(".")
        # Importing a module runs every package above it first.
        for depth in range(1, len(parts) + 1):
            module = ".".join(parts[:depth])
            if module not in seen and _source(module):
                seen.add(module)
                visit(_statements(module), module, False)
    return found


def test_the_api_registers_its_routers():
    """An unregistered router is a 404 on every one of its paths, which looks like a client bug."""
    pytest.importorskip("fastapi")
    import os

    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)
    from services.api.app import app

    # Asked of the app: the list of its routes is not served to anybody (`services.api.app`).
    paths = app.openapi()["paths"]

    for prefix in ("/account", "/admin", "/auth", "/ai-accounts", "/dashboard",
                   "/documents", "/feed", "/legal", "/onboarding", "/push", "/telegram"):
        assert any(path.startswith(prefix) for path in paths), f"{prefix} is not registered"


def test_the_api_serves_the_site_without_swallowing_its_own_routes(tmp_path):
    """FastAPI matches in registration order, so a mount at `/` registered before a route swallows it."""
    pytest.importorskip("fastapi")
    import importlib
    import os

    (tmp_path / "index.html").write_text("<!doctype html><title>site</title>", encoding="utf-8")
    os.environ["WEB_DIR"] = str(tmp_path)
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)

    from fastapi.testclient import TestClient

    import services.api.app as app_module

    app_module = importlib.reload(app_module)
    try:
        with TestClient(app_module.app) as http:
            assert http.get("/").status_code == 200, "the site is served at the root"
            assert http.get("/health").json()["status"] == "ok", "and the API still answers"
            assert http.get("/legal/privacy").status_code == 200
    finally:
        os.environ.pop("WEB_DIR", None)
        importlib.reload(app_module)


def test_a_missing_web_build_is_a_warning_not_a_silent_404(caplog, tmp_path):
    """An API answering 404 at `/` looks like a broken site rather than a missing build."""
    pytest.importorskip("fastapi")
    import importlib
    import logging
    import os

    os.environ["WEB_DIR"] = str(tmp_path / "does-not-exist")
    os.environ.setdefault("ACCESS_TOKEN_SECRET", "test-" + "x" * 40)

    import services.api.app as app_module

    with caplog.at_level(logging.WARNING):
        importlib.reload(app_module)
    try:
        assert any("no web build" in record.message for record in caplog.records)
    finally:
        os.environ.pop("WEB_DIR", None)
        importlib.reload(app_module)
