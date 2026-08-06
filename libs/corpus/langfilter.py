"""Drop postings written in a language the user cannot apply in."""
from __future__ import annotations

# Below this, detection is guesswork. A three-word title is not evidence of anything.
MIN_CHARS = 40

# Sources exempt from the filter entirely.
EXEMPT_SOURCES = frozenset({"hiringcafe"})


def detect_language(text: str) -> str | None:
    """ISO-639-1 code of the dominant language, or None when it cannot be determined."""
    text = (text or "").strip()
    if len(text) < MIN_CHARS:
        return None
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0
        return detect(text)
    except Exception:
        return None


def is_allowed(text: str, allowed: set[str] | None, *, source: str | None = None) -> bool:
    """True to keep the job."""
    if source and source in EXEMPT_SOURCES:
        return True
    if not allowed:
        return True
    language = detect_language(text)
    return language is None or language in allowed
