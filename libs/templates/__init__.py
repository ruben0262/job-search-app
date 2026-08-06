"""The CV layout, and the loader that reads it."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent

# One template, adapted per job by the tailoring prompt (decided 2026-07-28).
BASE_CV_TEMPLATE = "base_cv.html"


@lru_cache(maxsize=4)
def load(name: str) -> str:
    """Read a template file, with newlines normalised."""
    return (_HERE / name).read_text(encoding="utf-8").replace("\r\n", "\n")
