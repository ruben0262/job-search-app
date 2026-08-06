"""Generation prompts and the label/rubric templates."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent

LABEL_TEMPLATE = "label_template.md"


@lru_cache(maxsize=8)
def load(name: str) -> str:
    """Read a prompt file."""
    return (_HERE / name).read_text(encoding="utf-8").replace("\r\n", "\n")
