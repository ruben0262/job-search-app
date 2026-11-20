"""The browser the documents are printed with, as a process beside the platform's own."""
from __future__ import annotations

import os
import pathlib
import shutil

import pytest

from services.docgen import pdf

needs_chrome = pytest.mark.skipif(
    not pathlib.Path(pdf.chrome_binary()).exists() and shutil.which(pdf.chrome_binary()) is None,
    reason="no Chrome on this machine (set CHROME_BIN to run it)")
needs_proc = pytest.mark.skipif(not pathlib.Path("/proc/self/environ").exists(),
                                reason="reads a process's environment from /proc, which only Linux has")


def _environment_of_the_browser(process, within: float = 20.0) -> set[str]:
    """The names in the environment the browser itself runs with. It is started through a shell that becomes
    it, and for a moment on the way the process has no environment to read: asked for until it is the
    browser's, which is when its command line is."""
    import time

    of = pathlib.Path(f"/proc/{process.pid}")
    until = time.monotonic() + within
    while time.monotonic() < until:
        command, given = (of / "cmdline").read_bytes(), (of / "environ").read_bytes()
        if b"--remote-debugging-pipe" in command and not command.startswith(b"/bin/bash") and given:
            return {entry.split(b"=", 1)[0].decode() for entry in given.split(b"\0") if entry}
        time.sleep(0.02)
    raise AssertionError(f"the browser did not start within {within:.0f} s")


@needs_chrome
@needs_proc
def test_the_browser_is_started_without_the_platforms_keys(monkeypatch):
    """Chrome inherited the whole environment of the worker that starts it: the database's address and the
    key to every stored credential, in a browser run without its sandbox (2026-11-20). It is given where
    programs, its files and its fonts are, and nothing else."""
    monkeypatch.setenv("SECRETS_KEK", "k0:not-a-real-key-but-shaped-like-one")
    monkeypatch.setenv("MONGODB_URI", os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"))
    browser = pdf.Chrome()
    try:
        names = _environment_of_the_browser(browser.process)
    finally:
        browser.process.kill()

    assert "PATH" in names, "the premise: this is the browser's environment"
    assert not {"SECRETS_KEK", "MONGODB_URI"} & names
