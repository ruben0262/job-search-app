"""The switched-off model (CLAUDE.md §3): kept in the tree, started by nothing."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_switched_off_service_starts_only_under_the_ml_profile():
    """The import fence keeps live code off the model; this keeps a bare `docker compose up` off it."""
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    fence = next(contract for contract in contracts["tool"]["importlinter"]["contracts"]
                 if contract["name"] == "switched-off model")
    switched_off = {name.split(".")[1] for name in fence["forbidden_modules"]
                    if re.fullmatch(r"services\.\w+", name)}

    compose = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")
    services = compose.split("\nservices:\n", 1)[1].split("\nvolumes:", 1)[0]
    # A service is its name at two spaces and every line under it at four or more.
    blocks = dict(re.findall(r"^  (\w+):\n((?:(?:    .*)?\n)+)", services, re.MULTILINE))
    in_ml = {name for name, block in blocks.items() if 'profiles: ["ml"]' in block}

    assert switched_off, "the fence names no service, so this would prove nothing"
    assert in_ml == switched_off
