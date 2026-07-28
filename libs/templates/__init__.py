"""The CV layout, and the loader that reads it. **The layout is data, not code.**

Same split as ``libs.prompts``: the bytes live in a file so docgen, the tests, and anything that
inspects a user's document all read the same layout rather than each carrying a copy that drifts.
The difference is that a prompt is filled and sent, while this is filled and then *handed to the
model as the document to edit* — ``tailor_cv.md`` receives the rendered CV as its BASE TEMPLATE and
returns a tailored copy of it.

The layout itself is hand-tuned and load-bearing (A4 page rules, font paths, the ``@page`` margin
rule that stops Chrome drawing a header over the content), which is why it is maintained here and
never generated per user — ``PROFILE_CAPTURE_PLAN.md`` §6.5, *buy, do not generate*.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).parent

# One template, adapted per job by the tailoring prompt (decided 2026-07-28). The reference
# instance kept `dev` and `quant` and had the prompt choose between them, which is the same work
# done twice.
BASE_CV_TEMPLATE = "base_cv.html"


@lru_cache(maxsize=4)
def load(name: str) -> str:
    """Read a template file, with newlines normalised.

    Normalised for the same reason ``libs.prompts.load`` does it: a checkout on Windows must not
    produce different bytes from one on Linux, or two boxes rendering the same profile would
    disagree about a document that is supposed to be reproducible.
    """
    return (_HERE / name).read_text(encoding="utf-8").replace("\r\n", "\n")
