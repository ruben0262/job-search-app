"""Turning HTML into a PDF with headless Chrome, and checking what came out.

**One Chrome per worker, kept.** A browser launched for every document was the largest cost in a
build that was not the model: 0.74 s a launch on a developer machine and ~6 s on the box (measured
2026-08-07), against 0.12 s for a new tab in a browser that is already running. The document goes to
the tab over Chrome's DevTools pipe and the PDF comes back the same way, so nothing touches disk
(D9). The output was compared with `--print-to-pdf` before the switch: same pages, same A4 size,
same extracted text, and byte-identical page rasters for a one-page and a three-page document.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import pathlib
import select
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from libs.documents.read import EMAIL

logger = logging.getLogger(__name__)

# Chrome's own flags.
CHROME_FLAGS = ("--headless=old", "--no-sandbox", "--disable-gpu",
                "--disable-extensions", "--disable-background-networking",
                "--disable-component-update", "--disable-default-apps", "--metrics-recording-only",
                "--mute-audio", "--hide-scrollbars")

# The page size and margins from the document's own `@page` rule, its backgrounds, and no header or
# footer: what `--print-to-pdf` produced.
PRINT = {"printBackground": True, "preferCSSPageSize": True, "displayHeaderFooter": False}

# A build that has not produced a PDF in two minutes is not going to.
COMPILE_TIMEOUT_SEC = 120

# Below this many extracted characters the PDF is empty or image-based, whatever it looks like.
MIN_EXTRACTED_CHARS = 300

# A single-column CV extracts as normal prose lines.
MIN_AVG_LINE_CHARS = 8


class CompileFailed(RuntimeError):
    """Chrome did not produce a PDF. Retryable — it is usually memory or a cold start."""


@dataclass(frozen=True)
class PdfReport:
    pages: int | None
    chars: int
    issues: list[str] = field(default_factory=list)
    # What the last page holds: a second page with a few lines on it is a CV that spilled over.
    last_page_chars: int | None = None

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


class Chrome:
    """One headless Chrome on `--remote-debugging-pipe`: commands in on its fd 3, answers out on 4.

    It exits by itself when the pipe closes, so a worker that dies leaves no browser behind.
    """

    def __init__(self) -> None:
        to_chrome, self._send = os.pipe()
        self._receive, from_chrome = os.pipe()

        # Chrome reads on descriptor 3 and writes on 4, and a shell puts the pipes there after the fork. A
        # Python `preexec_fn` did until 2026-11-11, and it runs after the fork's hooks: pymongo's closes its
        # sockets in the child, so whenever they held 3 and 4 every launch failed with EBADF (found by the
        # CV bench, which connects first; a worker relaunching a dead Chrome is in the same place). bash,
        # because dash reads only single-digit descriptors and a busy worker's pipes are past 9.
        closes = "".join(f" {fd}<&-" for fd in (to_chrome, from_chrome) if fd not in (3, 4))
        self.process = subprocess.Popen(  # noqa: S603 - the binary is ours, the arguments literals
            ["/bin/bash", "-c", f'exec "$0" "$@" 3<&{to_chrome} 4>&{from_chrome}{closes}', chrome_binary(),
             *CHROME_FLAGS, "--remote-debugging-pipe", "about:blank"],
            pass_fds=(to_chrome, from_chrome), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(to_chrome)
        os.close(from_chrome)
        self._buffer, self._last = b"", 0

    def print(self, html: str) -> bytes:
        """A blank tab, the document, its fonts, the PDF, and the tab closed again."""
        deadline = time.monotonic() + COMPILE_TIMEOUT_SEC
        tab = self._call("Target.createTarget", {"url": "about:blank"}, deadline)["targetId"]
        try:
            session = self._call("Target.attachToTarget", {"targetId": tab, "flatten": True},
                                 deadline)["sessionId"]
            self._call("Page.setDocumentContent", {"frameId": tab, "html": html}, deadline, session)
            # Measured into pages only once its fonts are in, or a fallback font sets the length.
            self._call("Runtime.evaluate", {"expression": "document.fonts.ready.then(() => true)",
                                            "awaitPromise": True}, deadline, session)
            return base64.b64decode(self._call("Page.printToPDF", PRINT, deadline, session)["data"])
        finally:
            self._call("Target.closeTarget", {"targetId": tab}, deadline)

    def close(self) -> None:
        self.process.kill()
        self.process.wait()
        os.close(self._send)
        os.close(self._receive)

    def _call(self, method: str, params: dict, deadline: float, session: str | None = None) -> dict:
        """One command and its answer. Events arrive in between, and nothing here needs them."""
        self._last += 1
        command = {"id": self._last, "method": method, "params": params,
                   **({"sessionId": session} if session else {})}
        pending = memoryview(json.dumps(command).encode() + b"\0")
        while pending:
            pending = pending[os.write(self._send, pending):]
        while True:
            answer = self._read(deadline)
            if answer.get("id") == self._last:
                if "error" in answer:
                    raise CompileFailed(f"chrome refused {method}: {answer['error'].get('message')}")
                return answer["result"]

    def _read(self, deadline: float) -> dict:
        while b"\0" not in self._buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self._receive], [], [], left)[0]:
                raise CompileFailed(f"chrome did not answer within {COMPILE_TIMEOUT_SEC}s")
            chunk = os.read(self._receive, 1 << 16)
            if not chunk:
                raise CompileFailed(f"chrome exited mid-document (code {self.process.poll()})")
            self._buffer += chunk
        message, self._buffer = self._buffer.split(b"\0", 1)
        return json.loads(message)


_chrome: Chrome | None = None


def browser() -> Chrome:
    """This worker's browser: launched on first use, and again after one dies."""
    global _chrome  # noqa: PLW0603 - one per process, replaced only when it fails
    if _chrome is not None and _chrome.process.poll() is not None:
        logger.warning("docgen: chrome exited (code %s); starting another",
                       _chrome.process.returncode)
        _discard()
    if _chrome is None:
        _chrome = Chrome()
    return _chrome


def render(html: str) -> bytes:
    """One HTML document to the bytes of its PDF.

    A browser that failed in any way is thrown away, and the next render starts a new one: a
    retried build then gets a fresh Chrome rather than the one that was out of memory.
    """
    try:
        return browser().print(html)
    except (CompileFailed, OSError, ValueError) as exc:
        _discard()
        if isinstance(exc, CompileFailed):
            raise
        raise CompileFailed(f"chrome did not run: {type(exc).__name__}: {exc}") from exc


def _discard() -> None:
    global _chrome  # noqa: PLW0603
    if _chrome is not None:
        _chrome.close()
        _chrome = None


def inspect_pdf(pdf: bytes, *, kind: str = "cv", headings: tuple[str, ...] = ()) -> PdfReport:
    """Page count and the ATS readability check, from one read of the document. `headings` are the CV's
    own section titles, in the order it gives them."""
    try:
        from pypdf import PdfReader  # noqa: PLC0415 - only this path needs it
    except ImportError:
        return PdfReport(pages=None, chars=0, issues=["pypdf is not installed — checks skipped"])

    try:
        reader = PdfReader(io.BytesIO(pdf))
        texts = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - any parse failure is one outcome: unreadable
        return PdfReport(pages=None, chars=0, issues=[f"text extraction failed: {type(exc).__name__}"])

    report = inspect_pdf_text("\n".join(texts), kind=kind, headings=headings)
    return PdfReport(pages=len(texts), chars=report.chars, issues=report.issues,
                     last_page_chars=len(texts[-1].strip()) if texts else None)


def inspect_pdf_text(text: str, *, kind: str = "cv", headings: tuple[str, ...] = ()) -> PdfReport:
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
        # Each of the CV's own headings, where it belongs. Not two English words, as until 2026-11-11: a CV
        # is written in the posting's language and headed in it, and a German one has no "experience".
        flat, at = " ".join(text.split()).casefold(), 0
        for heading in headings:
            found = flat.find(" ".join(heading.split()).casefold(), at)
            if found < 0:
                issues.append(f"heading {heading!r} not found where it belongs in the extracted text")
            else:
                at = found + 1

        if not EMAIL.search(text):
            # Not fatal and not always wrong: a profile with no email link renders a header without one.
            issues.append("no email address in the extracted text — contact details may be "
                          "unreadable")

    return PdfReport(pages=None, chars=chars, issues=issues)
