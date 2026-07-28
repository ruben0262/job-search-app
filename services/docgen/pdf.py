"""Compiling HTML to PDF with headless Chrome, and checking what came out.

**Chrome is here and nowhere else on the platform** (`CODE_STRUCTURE.md` §5). It is the reason this
service has its own image: ~400MB of browser, spiky memory, and a cold start nobody else should pay
for.

Two checks run on every build, and neither of them is a formality:

- **The page count** is what the retry loop acts on. A three-page CV is not a stylistic complaint;
  the prompt's own hard rule is two pages of A4, and a recruiter scanning 200 applications does not
  reach page three.
- **The ATS text extraction** is the "paste it into Notepad" test. A PDF whose text comes out
  scrambled, or does not come out at all, is rejected by an automated screen before a human ever
  sees it — and it looks perfect on screen, which is exactly why it has to be checked mechanically.

The ATS result is a warning, never a build failure. The layout is a single column and known good;
this catches the unexpected, and refusing to deliver a CV because `pypdf` disliked something would
be the worse failure of the two.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Chrome's own flags. `--headless=old` because the new headless mode changed print behaviour
# mid-series; `--no-sandbox` because this runs as a container's only process with no user namespace,
# which is also why the container gets nothing else to run.
CHROME_FLAGS = ("--headless=old", "--no-sandbox", "--print-to-pdf-no-header", "--disable-gpu",
                "--disable-extensions", "--disable-background-networking",
                "--disable-component-update", "--disable-default-apps", "--metrics-recording-only",
                "--mute-audio", "--hide-scrollbars")

# A build that has not produced a PDF in two minutes is not going to. The lane's claim timeout is
# ten minutes and one build is two calls plus two compiles, so this has to be well inside it.
COMPILE_TIMEOUT_SEC = 120

# Below this many extracted characters the PDF is empty or image-based, whatever it looks like.
MIN_EXTRACTED_CHARS = 300

# A single-column CV extracts as normal prose lines. An average line this short means the text came
# out column-scrambled, which is what an ATS would receive.
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

    # A developer's Windows install, so the real-Chrome test in `tests/test_docgen.py` runs on the
    # machine the code is written on instead of only in CI. Production is the container, where
    # `CHROME_BIN` is set by the image and this is never reached.
    for candidate in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if pathlib.Path(candidate).exists():
            return candidate

    # Named rather than empty so the failure says what is missing instead of "file not found: ''".
    return "google-chrome"


def compile_pdf(source: pathlib.Path, output: pathlib.Path) -> pathlib.Path:
    """Render one local HTML file to a PDF. Raises `CompileFailed` if nothing usable came out.

    A `file://` URL rather than stdin: the document references a font family and Chrome resolves
    that against the filesystem it is given, so the page has to have a location.
    """
    url = "file://" + str(source.resolve()).replace("\\", "/")
    try:
        result = subprocess.run(  # noqa: S603 - the binary is ours and the URL is a local path
            [chrome_binary(), *CHROME_FLAGS, f"--print-to-pdf={output}", url],
            capture_output=True, timeout=COMPILE_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CompileFailed(f"chrome did not run: {type(exc).__name__}") from exc

    if not output.exists() or output.stat().st_size == 0:
        # Chrome's stderr is noisy and mostly irrelevant, but on a real failure the last line is the
        # reason. Truncated because the rest is GPU warnings on every single run.
        tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
        raise CompileFailed(f"chrome produced no PDF: {' '.join(tail)[:200]}")
    return output


def inspect_pdf(pdf: pathlib.Path) -> PdfReport:
    """Page count and the ATS readability check, from one read of the file.

    Degrades to `pages=None` rather than raising when `pypdf` is missing or the file will not parse.
    The caller treats an unknown page count as "do not retry on length", which is the right default:
    a retry costs the user another call, and "we could not count the pages" is not evidence that
    there are too many.
    """
    try:
        from pypdf import PdfReader  # noqa: PLC0415 - only this path needs it
    except ImportError:
        return PdfReport(pages=None, chars=0, issues=["pypdf is not installed — checks skipped"])

    try:
        reader = PdfReader(str(pdf))
        texts = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - any parse failure is one outcome: unreadable
        return PdfReport(pages=None, chars=0, issues=[f"text extraction failed: {type(exc).__name__}"])

    text = "\n".join(texts)
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

    lowered = text.casefold()
    for section in ("experience", "education"):
        if section not in lowered:
            issues.append(f"section {section!r} not found in the extracted text")

    if not _EMAIL.search(text):
        # Not fatal and not always wrong: a profile with no email link renders a header without one.
        issues.append("no email address in the extracted text — contact details may be unreadable")

    return PdfReport(pages=len(texts), chars=chars, issues=issues)
