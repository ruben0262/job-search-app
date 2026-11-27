"""`pytest --affected`: only the tests a change can reach (`tests/affected.py`, CLAUDE.md §7).

Its failure is silent by nature: a test left out does not fail, it just does not run. So what is held here is
that the tests which must be picked are, on a small project made for the purpose and on this one.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import affected

HERE = Path(__file__).resolve().parent
FILES = {
    "libs/__init__.py": "",
    "libs/prices.py": "from libs import rates\n\ndef price() -> int:\n    return rates.RATE * 2\n",
    "libs/rates.py": "RATE = 2\n",
    "libs/words/__init__.py": "from pathlib import Path\n\nTEXT = Path(__file__).with_name('hello.md')\n",
    "libs/words/hello.md": "hello\n",
    "libs/named.py": "NAMED = 1\n",
    "libs/words/later.py": "LATER = 1\n",
    "tools/alone.py": "ALONE = 1\n",
    "deploy/compose.yml": "services: {}\n",
    "tests/__init__.py": "",
    "tests/test_prices.py": "from libs.prices import price\n\ndef test_price():\n    assert price() == 4\n",
    "tests/test_words.py": "from libs import words\n\ndef test_words():\n    assert words.TEXT.exists()\n",
    "tests/test_named.py": ("import importlib\n\ndef test_named():\n"
                            "    assert importlib.import_module('libs.named').NAMED == 1\n"),
    "tests/test_deploy.py": ("from pathlib import Path\n\nDEPLOY = Path(__file__).parents[1] / 'deploy'\n\n"
                             "def test_deploy():\n    assert (DEPLOY / 'compose.yml').exists()\n"),
    "pyproject.toml": "[tool.pytest.ini_options]\npythonpath = ['.']\n",
    "notes.md": "notes\n",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",  # noqa: S603, S607
                    *args], check=True, capture_output=True)


def _forget() -> None:
    """The tree is read once a process: a test that changes it, or looks at another, has it read again."""
    for cached in (affected._modules, affected._reach, affected._imported):
        cached.cache_clear()


@pytest.fixture()
def project(tmp_path, monkeypatch) -> Path:
    """A small project of its own, committed, with this one's picker and fixtures beside its tests."""
    for name, text in FILES.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for name in ("affected.py", "conftest.py"):
        shutil.copy(HERE / name, tmp_path / "tests" / name)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "first")
    monkeypatch.setattr(affected, "ROOT", tmp_path)
    _forget()
    yield tmp_path
    _forget()


def _picked(*paths: str) -> list[str]:
    return [Path(test).name for test in affected.reached(list(paths))[0]]


def test_a_module_picks_the_tests_that_import_it_through_any_number_of_modules(project):
    """`test_prices` never names `rates`: it imports what imports it."""
    assert _picked("libs/rates.py") == ["test_prices.py"]
    assert _picked("libs/prices.py") == ["test_prices.py"]


def test_a_module_named_in_a_string_picks_the_test_that_names_it(project):
    """Imported by name, or patched by it (`monkeypatch.setattr("libs.x.y", ...)`): no `import` says so."""
    assert _picked("libs/named.py") == ["test_named.py"]


def test_a_file_that_is_not_python_picks_the_tests_of_the_modules_beside_it(project):
    """A prompt, a stylesheet, a list: read by its folder's code, so it is that code's tests which see it."""
    assert _picked("libs/words/hello.md") == ["test_words.py"]


def test_a_module_nothing_imports_picks_the_tests_of_the_modules_beside_it(project):
    """A module found by listing its folder, or one too new for anything to import: what would load it is
    beside it, and a pick by `import` lines alone would run nothing for it."""
    assert _picked("libs/words/later.py") == ["test_words.py"]


def test_a_test_that_names_a_folder_is_picked_by_everything_under_it(project):
    """What reads the tree imports none of it: the deploy files, the site's sources."""
    assert _picked("deploy/compose.yml") == ["test_deploy.py"]


def test_a_changed_test_is_picked_itself(project):
    assert _picked("tests/test_words.py") == ["test_words.py"]


@pytest.mark.parametrize("path", affected.EVERYTHING)
def test_what_decides_how_every_test_runs_picks_every_test(project, path):
    files, why, _ = affected.reached([path])
    assert len(files) == 4 and path in why


def test_a_python_file_that_is_gone_picks_every_test(project):
    """What imported it may still say so, and nothing else would run the test that finds out."""
    (project / "libs" / "rates.py").unlink()
    assert affected.changed() == ["libs/rates.py"]
    _forget()
    files, why, _ = affected.reached(affected.changed())
    assert len(files) == 4 and "libs/rates.py is gone" in why


def test_a_file_no_test_reaches_is_named_and_picks_nothing(project):
    files, _, unreached = affected.reached(["tools/alone.py", "notes.md", "libs/rates.py"])
    assert [Path(test).name for test in files] == ["test_prices.py"]
    assert unreached == ["tools/alone.py", "notes.md"]


def test_what_changed_is_what_is_edited_what_is_new_and_what_was_committed_since(project):
    (project / "libs" / "rates.py").write_text("RATE = 3\n", encoding="utf-8")
    (project / "libs" / "new.py").write_text("NEW = 1\n", encoding="utf-8")
    assert affected.changed() == ["libs/new.py", "libs/rates.py"]
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "second")
    assert affected.changed() == []
    assert affected.changed("HEAD~1") == ["libs/new.py", "libs/rates.py"]


def _run(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "tests", "-p", "no:cacheprovider", *args],  # noqa: S603
                          cwd=project, capture_output=True, text=True, check=False)


def test_the_flag_runs_the_tests_a_change_reaches_and_says_how_many_of_how_many(project):
    """The whole way: the flag, what changed, the pick, and pytest collecting only that."""
    (project / "libs" / "rates.py").write_text("RATE = 3\n", encoding="utf-8")
    ran = _run(project, "--affected", "-q", "-rf")
    assert "affected: 1 of 4 test files" in ran.stdout, "said however quiet the run: the gates run it with -q"
    assert "FAILED tests/test_prices.py::test_price" in ran.stdout, "the test of what changed ran, and saw it"
    assert "1 failed" in ran.stdout and "passed" not in ran.stdout, "and no other test did"
    assert ran.returncode == 1


def test_without_the_flag_every_test_runs(project):
    (project / "libs" / "rates.py").write_text("RATE = 3\n", encoding="utf-8")
    ran = _run(project, "-q")
    assert "1 failed, 3 passed" in ran.stdout


def test_a_change_no_test_reaches_is_a_pass_that_names_it(project):
    """Nothing to run is not a failure, and not silence either: the file is said to have no test."""
    (project / "notes.md").write_text("more\n", encoding="utf-8")
    ran = _run(project, "--affected", "-q")
    assert ran.returncode == 0, ran.stdout
    assert "affected: 0 of 4 test files" in ran.stdout and "no test reaches: notes.md" in ran.stdout


# ── on this project ─────────────────────────────────────────────────────────────────────────────────────────

def test_a_page_of_the_site_picks_the_browser_walk_and_a_job_board_does_not():
    """The two ends of what a pick is for: the walk is most of the suite's time, so it must run when the site
    changes and must not when a part the site never loads does."""
    assert "tests/test_e2e_site.py" in affected.reached(["web/src/App.tsx"])[0]
    assert "tests/test_e2e_site.py" in affected.reached(["services/api/app.py"])[0]
    boards = affected.reached(["services/harvester/__main__.py"])[0]
    assert "tests/test_harvest_cycle.py" in boards and "tests/test_e2e_site.py" not in boards
    bench = affected.reached(["scripts/cvlab/run.py"])[0]
    assert "tests/test_cvlab.py" in bench and "tests/test_e2e_site.py" not in bench


def test_the_tests_that_read_the_tree_are_picked_by_a_change_anywhere_in_it():
    """The relationship tests import little and read everything (CLAUDE.md §6): a setting's consumer, a
    column's producer, a deploy file. A pick by imports alone would never run them."""
    picked = affected.reached(["libs/membership.py"])[0]
    for test in ("test_settings_are_wired", "test_limits_are_wired", "test_columns_have_producers"):
        assert f"tests/{test}.py" in picked, test
    assert "tests/test_schedules.py" in affected.reached(["deploy/harvester.timer"])[0]
