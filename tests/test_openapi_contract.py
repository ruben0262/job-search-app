"""The committed API contract, and the one way it has broken.

CI regenerates `web/openapi.json` and fails the build if it differs from the committed copy. That
gate is only worth running if **the same command produces the same bytes on every machine**, and on
2026-08-06 it did not: Python's `write_text` translates line endings to the platform's, so a dump run
on Windows committed 4,642 CRLF lines and CI regenerated the identical document with LF. The diff
was the whole file and not one line had changed.

This is the same defect as `alembic>=1.13` letting CI resolve 1.19 while a laptop held 1.18.5
(ROADMAP 6al): **a gate whose result depends on which machine ran it is not a prediction of CI, it
is a coincidence.** The writer now pins the newline; this fails the build if a contract is ever
committed from a machine that did not.
"""
from __future__ import annotations

import json
import pathlib

CONTRACT = pathlib.Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def test_the_committed_contract_uses_the_line_endings_ci_generates():
    """**LF, because CI runs on Linux and rewrites this file to compare it.**

    A CRLF copy fails the contract gate on every push with a diff nobody can read, and the fix is
    invisible from the diff itself — every line appears changed and none is.
    """
    assert b"\r\n" not in CONTRACT.read_bytes(), (
        "web/openapi.json is CRLF; CI regenerates it as LF and the contract gate will fail with a "
        "whole-file diff. Re-run `npm run types` in web/ with the current dump_openapi.py.")


def test_the_contract_is_stable_enough_to_diff():
    """Sorted keys and a trailing newline. An unstable dump produces a diff on every run, and a diff
    that is always noisy is one nobody reads — which is how a real contract change gets waved
    through."""
    raw = CONTRACT.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    document = json.loads(raw)
    assert raw == json.dumps(document, indent=2, sort_keys=True) + "\n", (
        "the contract is not the canonical dump — regenerate it rather than hand-editing")
