"""Compiling HTML to PDF with headless Chrome, and checking what came out."""
from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Chrome's own flags.
CHROME_FLAGS = ("--headless=old", "--no-sandbox",
                "--print-to-pdf-no-header", "--no-pdf-header-footer", "--disable-gpu",
                "--disable-extensions", "--disable-background-networking",
                "--disable-component-update", "--disable-default-apps", "--metrics-recording-only",
                "--mute-audio", "--hide-scrollbars")

# A build that has not produced a PDF in two minutes is not going to.
COMPILE_TIMEOUT_SEC = 120

# Below this many extracted characters the PDF is empty or image-based, whatever it looks like.
MIN_EXTRACTED_CHARS = 300

# A single-column CV extracts as normal prose lines.
MIN_AVG_LINE_CHARS = 8

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.\w+")


class CompileFailed(RuntimeError):
    """Chrome did not produce a PDF. Retryable — it is usually memory or a cold start."""


@dataclass(frozen=True)
class PdfReport:
    pages: int | None
    chars: int
    issues: list[str] = field(default_factory=list)

    @property
    def ats_ok(self) -> bool:
        return not self.issues


def chrome_binary() -> str:
    """Where Chrome is. `CHROME_BIN` wins so the image can pin it and a dev box can point at its own."""
    if binary := os.environ.get("CHROME_BIN"):
        return binary
    found = (shutil.which("google-chrome") or shutil.which("chromium")
             or shutil.which("chromium-browser"))
    if found:
        return found

    # A developer's Windows install, so the real-Chrome test runs where the code is written.
    for candidate in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if pathlib.Path(candidate).exists():
            return candidate

    # Named rather than empty so the failure says what is missing instead of "file not found: ''".
    return "google-chrome"


def compile_pdf(source: pathlib.Path, output: pathlib.Path) -> pathlib.Path:
    """Render one local HTML file to a PDF."""
    url = "file://" + str(source.resolve()).replace("\\", "/")
    try:
        result = subprocess.run(  # noqa: S603 - the binary is ours and the URL is a local path
            [chrome_binary(), *CHROME_FLAGS, f"--print-to-pdf={output}", url],
            capture_output=True, timeout=COMPILE_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CompileFailed(f"chrome did not run: {type(exc).__name__}") from exc

    if not output.exists() or output.stat().st_size == 0:
        # Chrome's stderr is noisy and mostly irrelevant, but on a real failure the last line is the reason.
        tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
        raise CompileFailed(f"chrome produced no PDF: {' '.join(tail)[:200]}")
    return output


def inspect_pdf(pdf: pathlib.Path, *, kind: str = "cv") -> PdfReport:
    """Page count and the ATS readability check, from one read of the file."""
    try:
        from pypdf import PdfReader  # noqa: PLC0415 - only this path needs it
    except ImportError:
        return PdfReport(pages=None, chars=0, issues=["pypdf is not installed — checks skipped"])

    try:
        reader = PdfReader(str(pdf))
        texts = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - any parse failure is one outcome: unreadable
        return PdfReport(pages=None, chars=0, issues=[f"text extraction failed: {type(exc).__name__}"])

    report = inspect_pdf_text("\n".join(texts), kind=kind)
    return PdfReport(pages=len(texts), chars=report.chars, issues=report.issues)


def inspect_pdf_text(text: str, *, kind: str = "cv") -> PdfReport:
    """The readability checks, over already-extracted text."""
    issues: list[str] = []
    chars = len(text.strip())

    if chars < MIN_EXTRACTED_CHARS:
        issues.append(f"only {chars} characters extracted — the PDF may be image-based or empty")

    lines = [line for line in text.split("\n") if line.strip()]
    if lines:
        average = sum(len(line) for line in lines) / len(lines)
        if average < MIN_AVG_LINE_CHARS:
            issues.append(f"average line length {average:.0f} characters — the text may be "
                          f"column-scrambled, which is what an ATS would receive")

    if kind == "cv":
        lowered = text.casefold()
        for section in ("experience", "education"):
            if section not in lowered:
                issues.append(f"section {section!r} not found in the extracted text")

        if not _EMAIL.search(text):
            # Not fatal and not always wrong: a profile with no email link renders a header without one.
            issues.append("no email address in the extracted text — contact details may be "
                          "unreadable")

    return PdfReport(pages=None, chars=chars, issues=issues)
