"""Generation prompts and the label/rubric templates."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent

LABEL_TEMPLATE = "label_template.md"


@lru_cache(maxsize=8)
def load(name: str) -> str:
    """Read a prompt file."""
    return (_HERE / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def version(name: str) -> str:
    """Which wording of a prompt this is: the first twelve hex digits of its text's SHA-256. Kept with every
    document it writes (`BuiltDocument.prompt_version`), so what people say happened to their applications
    is read wording by wording (`libs.tenant.applications.outcomes`). A changed prompt's CHANGELOG entry
    names the version it became."""
    return hashlib.sha256(load(name).encode("utf-8")).hexdigest()[:12]
