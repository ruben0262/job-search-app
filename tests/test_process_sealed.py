"""What the document worker holds is not readable by the programs it starts (`libs.secrets.process`)."""
from __future__ import annotations

import subprocess
import sys

import pytest

# A process with a secret in its environment starts a child that tries to read that environment, as any
# program run by the same user may; then says it keeps to itself, and starts another that tries again.
PROBE = """
import os, subprocess, sys
from libs.secrets.process import keep_to_itself

reads = "import sys; print(open(f'/proc/{sys.argv[1]}/environ', 'rb').read().count(b'KEK-OF-THE-TEST'))"
read = [sys.executable, "-c", reads, str(os.getpid())]
before = subprocess.run(read, capture_output=True, text=True)
kept = keep_to_itself()
after = subprocess.run(read, capture_output=True, text=True)
print(before.stdout.strip(), kept, after.returncode, "PermissionError" in after.stderr)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="the worker runs on Linux, and so does what protects it")
def test_a_program_the_worker_starts_cannot_read_the_key_out_of_it():
    """The worker's environment holds the key to every stored credential, and an AI tool it starts can be
    told what to do by the text of a job posting, which anybody can write. A child may read its parent's
    environment, being the same user: here one does, then cannot once the parent keeps to itself."""
    ran = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=60,  # noqa: S603
                         env={"SECRETS_KEK": "KEK-OF-THE-TEST", "PYTHONPATH": "."}, check=True)

    read_before, kept, refused, why = ran.stdout.split()
    assert read_before == "1", "the premise: a child reads its parent's environment"
    assert (kept, refused != "0", why) == ("True", True, "True")


def test_the_document_worker_keeps_to_itself_before_it_starts_anything(monkeypatch):
    """The browser is the first program the worker starts and the AI tools come after: it is unreadable to
    its children before the first of them exists."""
    from services.docgen import __main__ as worker

    done: list[str] = []
    for name in ("configure", "install", "ensure_schema", "database"):
        monkeypatch.setattr(worker, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "keep_to_itself", lambda: done.append("kept to itself"))
    monkeypatch.setattr(worker.pdf, "browser", lambda: done.append("browser started"))
    monkeypatch.setattr(worker, "run_worker", lambda *args, **kwargs: done.append("jobs taken"))

    assert worker.main() == 0
    assert done == ["kept to itself", "browser started", "jobs taken"]
