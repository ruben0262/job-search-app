"""Drop postings written in a language the user cannot apply in."""
from __future__ import annotations

import logging
import threading
from functools import cache

logger = logging.getLogger(__name__)

# One reading at a time. langdetect loads its language profiles into one shared factory on first use, and
# threads that start together read it half-loaded: eight English CVs read at once came back as None seven
# times and as Norwegian twice (2026-11-11). The harvester describes on two threads, LinkedIn beside the
# boards, and the CV bench checks on several.
_READING = threading.Lock()

# Below this, detection is guesswork. A three-word title is not evidence of anything.
MIN_CHARS = 40

@cache
def _warn_library_missing() -> None:
    """Said once per process, not once per posting.

    The answer cannot change while the process runs, and a line per row would bury it (§8).
    """
    logger.error(
        "langdetect is not installed in this image, so every posting is stored with no language "
        "and every account's language filter admits everything. Add it to this service's "
        "requirements.txt")


def detect_language(text: str) -> str | None:
    """ISO-639-1 code of the dominant language, or None when it cannot be determined.

    **A missing library and an unreadable posting are not the same answer, and treating them as one
    silently disabled this filter for the whole platform.** `langdetect` was declared only in the
    notifier's image; the code that calls this moved to the harvester and the dependency did not
    follow. Every `ImportError` was caught by the same blind `except` as a genuine detection failure
    and returned `None`, `is_allowed` keeps `None` on purpose, and so every posting passed every
    account's language filter.

    Measured on the live platform 2026-08-11: on 2026-08-06, 608 of one account's delivered jobs had
    a detected language and 2 did not. By 2026-08-10 it was 1 and 544. Nothing raised, nothing was
    logged, and the setting in Your Details went on being saved and displayed the whole time.
    """
    text = (text or "").strip()
    if len(text) < MIN_CHARS:
        return None

    try:
        from langdetect import DetectorFactory, detect
    except ImportError:
        _warn_library_missing()
        return None

    try:
        with _READING:
            DetectorFactory.seed = 0
            return detect(text)
    except Exception:
        # A posting this cannot read is data, not an error: `is_allowed` keeps an unknown language
        # deliberately, because hiding a job somebody wanted is worse than showing one they did not.
        return None


def is_allowed(language: str | None, allowed: set[str] | None) -> bool:
    """True to keep the job. `language` is the stored code, read once at describe time."""
    if not allowed:
        return True
    # Unknown keeps: a wrong language hides a job somebody wanted, an unknown one costs nothing.
    return language is None or language in allowed
