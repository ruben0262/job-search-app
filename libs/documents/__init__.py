"""Uploaded documents, as text.

**The text is kept; the file is not.** Nothing downstream reads a PDF, and keeping a stranger's
diplomas and references on disk would be a liability with no consumer — `D9` makes generated
documents ephemeral and the same reasoning applies harder to documents the platform did not create.

What this feeds: the *process data* step, which is the only thing that reads it
(`ONBOARDING_AND_PROCESSING.md` §1). Upload happens during onboarding and costs nothing; processing
happens later, on the dashboard, and spends the user's credential.
"""
from libs.documents.extract import (
    MAX_FILE_BYTES,
    MAX_TEXT_CHARS,
    SUPPORTED,
    Extracted,
    UnsupportedDocument,
    extract_text,
)

__all__ = [
    "MAX_FILE_BYTES",
    "MAX_TEXT_CHARS",
    "SUPPORTED",
    "Extracted",
    "UnsupportedDocument",
    "extract_text",
]
