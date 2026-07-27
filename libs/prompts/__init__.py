"""Generation prompts and the label/rubric templates. **SACRED** — change control applies.

Carried over unchanged from the reference instance (CODE_STRUCTURE.md §7.4): no edit without
explicit approval, A/B measured, changelogged. The rule now also covers ``label_template.md``,
because editing that template re-versions **every user** — the template hash is what scopes each
user's training data, so a casual wording change silently invalidates labels and models across the
whole platform at once.

Prompt text is data, not code. It lives in ``libs`` so trainer, scorer, docgen, and capture all
read the same bytes rather than each shipping a copy that drifts.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent

LABEL_TEMPLATE = "label_template.md"


@lru_cache(maxsize=8)
def load(name: str) -> str:
    """Read a prompt file. Cached, because the template is read once per rubric render.

    Newlines are normalised on read: the template is data whose *bytes* determine every user's
    ``profile_version``, and a checkout on Windows must not produce different hashes from one on
    Linux. That would look like every user's labels spontaneously invalidating on a deploy.
    """
    return (_HERE / name).read_text(encoding="utf-8").replace("\r\n", "\n")
