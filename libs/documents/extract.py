"""Turning an uploaded file into text.

**The extracted text is kept; the original file is not.** Nothing downstream reads a PDF — the
capture conversation reads text, and so does everything after it. Keeping a stranger's diploma and
passport-adjacent paperwork on disk would be a liability with no consumer, and `D9` already makes
generated documents ephemeral; the same reasoning applies harder to documents the platform did not
create. Extraction happens in memory during the request and the bytes are never written down.

The cost, stated plainly: extraction cannot be re-run later with a better parser. If a PDF extracts
badly the user re-uploads it. That is the trade, and it is the right way round for a product holding
other people's personal records.

Every file here is **untrusted input** — a PDF is a program format and a `.docx` is a zip archive.
The defences are bounds rather than inspection: a size ceiling before parsing, a character ceiling
after it, and no format that requires executing anything.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 10 MB is a generous CV and a large scanned reference. Above it, something else is going on.
MAX_FILE_BYTES = 10 * 1024 * 1024
# Roughly 60 pages of prose. A capture conversation cannot use more, and an unbounded value is what
# turns a decompression bomb into an outage.
MAX_TEXT_CHARS = 200_000

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
        # Logged in full, reported briefly: a parser's exception text is meaningless to the person
        # at the form and occasionally quotes file contents.
        logger.warning("could not extract text from %s", filename, exc_info=True)
        raise UnsupportedDocument(
            f"{filename} could not be read — it may be corrupt, password-protected, or a scan "
            f"with no text layer") from exc

    text = _normalise(text)
    if not text.strip():
        # A scanned CV is a real and common case, and it extracts to nothing. Saying so is the
        # difference between the user re-uploading a text version and wondering why capture is bad.
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
    """UTF-8, falling back to latin-1 rather than failing.

    A CV exported from an old word processor is often cp1252, and latin-1 decodes every byte
    sequence — so the worst case is a few odd characters rather than a rejected upload.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _pdf(data: bytes) -> str:
    import io  # noqa: PLC0415

    from pypdf import PdfReader  # noqa: PLC0415 - only the api image carries a PDF parser

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        # Trying an empty password first is worth it — plenty of PDFs are "encrypted" with no
        # password purely to discourage editing.
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
    # Tables carry real content in a CV — dates, employers, and skills matrices are routinely laid
    # out in one, and reading only paragraphs silently drops them.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _normalise(text: str) -> str:
    """Collapse the whitespace a PDF extractor leaves behind, without joining separate lines.

    Line structure carries meaning in a CV — a list of roles is a list because of the line breaks —
    so runs of blank lines collapse to one and trailing spaces go, but lines stay lines.
    """
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()
