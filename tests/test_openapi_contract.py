"""The committed API contract, and the one way it has broken."""
from __future__ import annotations

import json
import pathlib

CONTRACT = pathlib.Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def test_the_committed_contract_uses_the_line_endings_ci_generates():
    """LF, because CI runs on Linux and rewrites this file to compare it."""
    assert b"\r\n" not in CONTRACT.read_bytes(), (
        "web/openapi.json is CRLF; CI regenerates it as LF and the contract gate will fail with a "
        "whole-file diff. Re-run `npm run types` in web/ with the current dump_openapi.py.")


def test_the_contract_is_stable_enough_to_diff():
    """Sorted keys and a trailing newline."""
    raw = CONTRACT.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    document = json.loads(raw)
    assert raw == json.dumps(document, indent=2, sort_keys=True) + "\n", (
        "the contract is not the canonical dump — regenerate it rather than hand-editing")
