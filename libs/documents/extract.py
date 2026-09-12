"""Turning an uploaded file into text."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 10 MB is a generous CV and a large scanned reference. Above it, something else is going on.
MAX_FILE_BYTES = 10 * 1024 * 1024

# **The one ceiling on how much of a person's own writing this platform holds, and therefore reads.**
# Roughly 90 pages of prose. Raised from 200,000 on 2026-08-13 together with the removal of a second,
# lower cap: `libs/agents/tools.py` re-truncated every `read_document` at 60,000, so an account whose
# uploads were stored in full still had them cut before the setup agent saw them. Measured on the live
# platform that day: one account's 145,493-character context file was read to 41%, and the two headings
# in the unread remainder were "KEY TECHNICAL ACHIEVEMENTS" and "SKILLS DEMONSTRATED" — the material
# the base CV is built from. Nothing errored, and the truncation notice reached the model rather than
# the person who uploaded it.
#
# So: one number, applied once, at the point the text is stored. Whatever is kept is what is read.
MAX_TEXT_CHARS = 300_000

SUPPORTED = (".txt", ".md", ".pdf", ".docx")


class UnsupportedDocument(ValueError):
    """The file cannot be read. Carries a message written for the person who uploaded it."""


@dataclass(frozen=True)
class Extracted:
    text: str
    chars: int
    sha256: str
    truncated: bool = False


def extract_text(filename: str, data: bytes) -> Extracted:
    """Read one uploaded file. Raises `UnsupportedDocument` with something the user can act on."""
    if len(data) > MAX_FILE_BYTES:
        raise UnsupportedDocument(
            f"this file is {len(data) // (1024 * 1024)} MB; the limit is "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB")

    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in SUPPORTED:
        raise UnsupportedDocument(
            f"{filename} is not a format this can read — use {', '.join(SUPPORTED)}")

    readers = {".txt": _plain, ".md": _plain, ".pdf": _pdf, ".docx": _docx}
    try:
        text = readers[suffix](data)
    except UnsupportedDocument:
        raise
    except Exception as exc:
        # Logged in full, reported briefly: a parser's exception occasionally quotes file contents.
        logger.warning("could not extract text from %s", filename, exc_info=True)
        raise UnsupportedDocument(
            f"{filename} could not be read — it may be corrupt, password-protected, or a scan "
            f"with no text layer") from exc

    text = _normalise(text)
    if not text.strip():
        # A scanned CV is a real and common case, and it extracts to nothing.
        raise UnsupportedDocument(
            f"{filename} contains no readable text — if it is a scan, upload a text version")

    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        logger.warning("%s extracted to %d characters; keeping the first %d",
                       filename, len(text), MAX_TEXT_CHARS)
        text = text[:MAX_TEXT_CHARS]

    return Extracted(text=text, chars=len(text),
                     sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                     truncated=truncated)


def _plain(data: bytes) -> str:
    """UTF-8, falling back to latin-1 rather than failing."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _pdf(data: bytes) -> str:
    import io  # noqa: PLC0415

    from pypdf import PdfReader  # noqa: PLC0415 - only the api image carries a PDF parser

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        # An empty password is worth trying: plenty of PDFs are encrypted only to discourage editing.
        try:
            reader.decrypt("")
        except Exception as exc:
            raise UnsupportedDocument(
                "this PDF is password-protected — save an unprotected copy and upload that") from exc

    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _docx(data: bytes) -> str:
    import io  # noqa: PLC0415

    import docx  # noqa: PLC0415

    document = docx.Document(io.BytesIO(data))
    parts = [paragraph.text for paragraph in document.paragraphs]
    # Tables carry real content in a CV, and reading only paragraphs silently drops them.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _normalise(text: str) -> str:
    """Collapse the whitespace a PDF extractor leaves behind, without joining separate lines."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()
