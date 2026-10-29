"""The CV layout, and the loader that reads it.

**There is no template here any more.** `base_cv.html` in this package was the marker-filled layout
`cv.py` rendered from `ProfileSchema` — `<!--NAME-->`, `<!--EXPERIENCE-->` and the rest. That path is
gone: a CV is now built from the person's own `base_cv.html` profile file, seeded from
`libs/scaffolds/base_cv.html` and written for them by the setup agent (`services/docgen/run.py`).

Deleted 2026-08-14, with the evidence, because it was the second copy of the same thing and the
copy nobody used. Measured on the live platform first: both accounts' base CVs carry the scaffold's
fourteen class names, its `@page` rule and its typography, and neither carries this file's — which
had seven of those classes and no `.entry`, `.profile` or `.competencies` at all. Its only caller
was `services.docgen.build.template_base()`, whose only caller was a test.

`load()` stays. It is the newline-normalising reader for anything else this package comes to hold,
and it is what `tests/test_cv_template.py` uses to read the scaffold the same way docgen does.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# The one base CV a new account is seeded with, and the one every user's own copy descends from.
SCAFFOLD_CV = Path(__file__).resolve().parents[1] / "scaffolds" / "base_cv.html"


@lru_cache(maxsize=4)
def load(path: Path | str) -> str:
    """Read a template file, with newlines normalised."""
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


STYLE = re.compile(r"(?is)<style\b.*?</style>")


def split_style(html: str) -> tuple[str, str]:
    """The document without its comments and its first `<style>` block, and that block ("" if none).

    Comments go first. The scaffold's own says "Keep the <style> block intact", and a search starting
    there took the comment's end, `<head>` and the placeholder `<title>` along with the CSS — the model
    never saw `<body>`, and every AI CV came back carrying `[YOUR NAME]`. One definition for the instant
    documents and docgen, which had each found the block their own way.
    """
    html = re.sub(r"(?s)<!--.*?-->", "", html)
    match = STYLE.search(html)
    return (html[:match.start()] + html[match.end():], match.group(0)) if match else (html, "")
