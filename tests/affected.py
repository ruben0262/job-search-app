"""Which test files a change can reach (`pytest --affected`, CLAUDE.md §7).

Read off the code as it stands, on every run: there is no recorded map, so there is none to go stale. A
changed file reaches a test file in two ways.

- **The test imports it**, itself or through what it imports, by an `import` or by its name in a string
  (`"services.api.app"`, as `importlib` and `monkeypatch` take it). A file that is not Python (a prompt, a
  stylesheet, a list of places) counts as the modules of its folder, which are what read it. So does a module
  nothing imports: one found by listing its folder, as every model is, or one too new to be imported yet.
- **The test reads that part of the tree.** A test that names a top folder as a path (`ROOT / "deploy"`,
  `"web"`) is reached by everything under it. That is how the relationship tests work (settings wired, columns
  produced), and why they run on nearly every change.

What decides how every test runs reaches every test, and so does a Python file that is gone: what imported it
may no longer say so. A changed file no test reaches is named beside the run's counts, never passed over in
silence: nothing covers it, which is worth knowing.
"""
from __future__ import annotations

import ast
import re
import subprocess
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DOTTED = re.compile(r"[A-Za-z_]\w*(\.\w+)+")
# How every test runs: the fixtures, pytest's own settings, and the versions it all runs on.
EVERYTHING = ("tests/conftest.py", "pyproject.toml", "requirements-dev.txt", "deploy/docker/constraints.txt")


def _git(*args: str) -> list[str]:
    done = subprocess.run(["git", "-C", str(ROOT), *args, "-z"],  # noqa: S603, S607 - git, from PATH, on names
                          capture_output=True, text=True, check=True)
    return [path for path in done.stdout.split("\0") if path]


def changed(since: str = "HEAD") -> list[str]:
    """Every path that differs from `since`: committed after it, staged, not staged, and new. A file moved is
    its old name gone and its new name added."""
    return sorted({*_git("diff", "--name-only", "--no-renames", since),
                   *_git("ls-files", "--others", "--exclude-standard")})


@cache
def _modules() -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """Each Python file of the project, with the project files it imports and the first part of every path it
    names. `retired/` is what the project no longer runs."""
    listed = (*_git("ls-files", "*.py"), *_git("ls-files", "--others", "--exclude-standard", "*.py"))
    files = {path for path in listed if not path.startswith("retired/") and (ROOT / path).is_file()}

    def found(dotted: str) -> list[str]:
        """The files a dotted name means: the module, and every package on the way to it."""
        parts = dotted.split(".")
        return [path for end in range(1, len(parts) + 1)
                for path in ("/".join(parts[:end]) + ".py", "/".join(parts[:end]) + "/__init__.py")
                if path in files]

    def read(path: str) -> tuple[frozenset[str], frozenset[str]]:
        package = path.split("/")[:-1]
        imports: set[str] = set()
        named: set[str] = set()
        for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.update(found(alias.name))
            elif isinstance(node, ast.ImportFrom):
                base = package[:len(package) - node.level + 1] if node.level else []
                module = ".".join([*base, *([node.module] if node.module else [])])
                imports.update(found(module))
                for alias in node.names:
                    imports.update(found(f"{module}.{alias.name}"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                named.add(node.value.split("/", 1)[0])
                if _DOTTED.fullmatch(node.value):  # a module in a string: imported by name, or patched
                    imports.update(found(node.value))
        return frozenset(imports - {path}), frozenset(named)

    return {path: read(path) for path in files}


@cache
def _reach(test: str) -> tuple[frozenset[str], frozenset[str]]:
    """Everything a test file imports, through any number of modules, and every path it names itself."""
    modules = _modules()
    seen, waiting = {test}, [test]
    while waiting:
        for module in modules[waiting.pop()][0] - seen:
            seen.add(module)
            waiting.append(module)
    return frozenset(seen), modules[test][1]


def tests() -> list[str]:
    return sorted(path for path in _modules() if path.startswith("tests/test_") and path.count("/") == 1)


@cache
def _imported() -> frozenset[str]:
    return frozenset().union(*(imports for imports, _ in _modules().values()))


def _beside(path: str) -> set[str]:
    """The modules of the nearest folder that has any: what reads a file that no `import` names."""
    modules, folder = _modules(), Path(path).parent
    while folder != Path():
        if here := {module for module in modules if Path(module).parent == folder}:
            return here
        folder = folder.parent
    return set()


def reached(paths: list[str]) -> tuple[list[str], str | None, list[str]]:
    """`(test files, why it is all of them, changed files no test reaches)` for these changed paths."""
    every = tests()
    for path in paths:
        if path in EVERYTHING:
            return every, f"{path} decides how every test runs", []
        if path.endswith(".py") and not path.startswith("retired/") and path not in _modules():
            return every, f"{path} is gone, and what imported it may not say so", []
    picked: set[str] = set()
    unreached: list[str] = []
    for path in paths:
        parts = path.split("/")
        # A test's own data (`tests/golden/...`) is named by its folder; everything else by its top folder.
        name = parts[1] if parts[0] == "tests" and len(parts) > 1 else parts[0]
        sources = ({path} if path in _imported() or path in every
                   else set() if parts[0] == "tests" else {path} | _beside(path))
        hit = {test for test in every
               if sources & _reach(test)[0] or name in _reach(test)[1]}
        if not hit and not path.startswith("retired/"):
            unreached.append(path)
        picked |= hit
    return sorted(picked), None, unreached
