"""A file of the type a document was uploaded as, written from the text read out of it (owner, 2026-11-10:
"download in the same format as the uploaded file").

For uploads from before that day, whose file was not kept (`UploadedFile`): a .pdf comes back a PDF, a
.docx a Word file, a .txt or .md as text. Their words and lines, not their layout: the layout left with
the file, which was never stored.
"""
from __future__ import annotations

import io
from pathlib import Path, PurePath

# A font with the letters a CV is written in (Polish, Czech, Greek, Cyrillic, …), for the PDF: the PDF's own
# fonts have Western European letters only. From `fonts-dejavu-core`, which the api image installs.
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def as_file(filename: str, text: str) -> bytes:
    """`text`, as a file of the type `filename` names."""
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".pdf":
        return _pdf(text)
    if suffix == ".docx":
        return _docx(text)
    return text.encode()


def _pdf(text: str) -> bytes:
    from fpdf import FPDF  # noqa: PLC0415 - only the download of an older upload needs it

    pdf = FPDF(format="A4")
    pdf.set_margins(20, 20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font("body", fname=str(FONT))
    pdf.set_font("body", size=11)
    pdf.add_page()
    pdf.multi_cell(0, 5.5, text)
    return bytes(pdf.output())


def _docx(text: str) -> bytes:
    import docx  # noqa: PLC0415

    document = docx.Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()
