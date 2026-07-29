"""Drop postings written in a language the user cannot apply in. Port of `pipeline/lib/langfilter.py`.

The reference added this because French, German, Polish and Slovak descriptions kept arriving and
an English CV is not going to be read for any of them. Scoring them costs money and delivering them
costs the user's attention, so they are dropped before either.

**Per user here, not per process.** The reference reads `JOB_LANGUAGES` from the environment
because it serves one person; the languages are a property of the *candidate*, so on a platform
they come from the profile — the same list the cover letter's `<<LANGUAGES>>` is filled from. One
fact, one place.

**Fails open, always.** Text too short to detect, `langdetect` not installed, an unrecognised
result — all keep the job. The asymmetry is deliberate and is the same one every filter in this
package uses: a false drop loses a real job silently and forever, a false keep costs one cheap
local score.
"""
from __future__ import annotations

# Below this, detection is guesswork. A three-word title is not evidence of anything.
MIN_CHARS = 40

# Sources exempt from the filter entirely. hiring.cafe aggregates direct-apply roles where the
# posting's language says less about whether an application is welcome than it does elsewhere.
EXEMPT_SOURCES = frozenset({"hiringcafe"})


def detect_language(text: str) -> str | None:
    """ISO-639-1 code of the dominant language, or None when it cannot be determined.

    Seeded, because `langdetect` is stochastic and an unseeded detector can return different
    answers for the same posting on two runs — which would show up as a job appearing and
    disappearing from a feed for no reason anybody could reproduce.
    """
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
    """True to keep the job.

    `allowed` is the user's language codes. `None` disables the filter, and so does an empty set —
    both mean "no opinion", which is the right reading for an account that has not said.
    """
    if source and source in EXEMPT_SOURCES:
        return True
    if not allowed:
        return True
    language = detect_language(text)
    return language is None or language in allowed
