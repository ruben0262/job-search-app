"""What can be measured about one tailored CV without asking a model.

Deliberately narrow. These are the rules `tailor_cv.md` states as literal properties of the text —
person, punctuation, entry and bullet counts, length — so a variant that claims to fix one of them
can be checked for free, repeatably, on every sample. Everything a regular expression cannot reach
(is the profile paragraph any good, does the CV answer this posting) is the judge's job.

`libs/scoring/cveval.py` used to hold checks like these and was deleted in the dead-code sweep,
correctly: it had one user's portfolio host and one user's language list baked into it. Nothing here
knows who the candidate is.
"""
from __future__ import annotations

import re

# The docgen worker's own detections (`services/docgen/checks.py`), so the bench counts what production
# sends back. First person there skips "I/O" and a title's numeral ("Design Engineer I"), which counted as
# the pronoun here once and handed a draft a win it did not earn.
from services.docgen.checks import cliches, first_person

# The em-dash rule the prompt states: banned in bullets and the profile paragraph, allowed in an
# entry's title header. Measured only where it is banned.
_EMDASH = re.compile(r"—|&mdash;|&#8212;")

_LI = re.compile(r"(?is)<li\b[^>]*>(.*?)</li>")
_PROFILE = re.compile(r'(?is)<p\b[^>]*class="[^"]*profile[^"]*"[^>]*>(.*?)</p>')
_ENTRY = re.compile(r'(?is)class="entry"')
_TAGS = re.compile(r"(?s)<[^>]+>")
_STYLE = re.compile(r"(?is)<style\b.*?</style>")

# A2 at 10pt in this layout runs about 3,600 characters of extracted text per page. Only ever used
# as a fitting signal — the real page count needs Chrome, which the bench does not have.
CHARS_PER_PAGE = 3_600


def text_of(html: str) -> str:
    """The document as a reader sees it: no style block, no tags, whitespace collapsed."""
    return re.sub(r"\s+", " ", _TAGS.sub(" ", _STYLE.sub(" ", html))).strip()


def body_parts(html: str) -> tuple[list[str], str]:
    """The bullets and the profile paragraph — the two places most rules apply."""
    bullets = [re.sub(r"\s+", " ", _TAGS.sub("", part)).strip() for part in _LI.findall(html)]
    profile = _PROFILE.search(html)
    return bullets, (re.sub(r"\s+", " ", _TAGS.sub("", profile.group(1))).strip() if profile else "")


def check(html: str) -> dict:
    """Every countable property of one CV, as one flat dict."""
    bullets, profile = body_parts(html)
    text = text_of(html)
    # A competency line is "Group: a, b, c" and its colon is the format, not prose. Excluded from
    # the colon count for the same reason the prompt excludes entry titles from the em-dash rule.
    competency = [b for b in bullets if ":" in b[:40]]
    experience = [b for b in bullets if b not in competency]

    profile_hits = first_person(profile)
    body_hits = profile_hits + sum(first_person(bullet) for bullet in experience)

    return {
        "chars": len(text),
        "est_pages": round(len(text) / CHARS_PER_PAGE, 2),
        "entries": len(_ENTRY.findall(html)),
        "bullets": len(experience),
        "competency_lines": len(competency),
        "profile_words": len(profile.split()),
        # The two drafts under test, as numbers.
        "first_person": body_hits,
        "profile_first_person": profile_hits,
        "colon_bullets": sum(1 for b in experience if ":" in b),
        # Rules the live prompt already states, carried as a regression: a variant must not win on
        # the drafts by breaking something that was working.
        "emdash_violations": (len(_EMDASH.findall(profile))
                              + sum(len(_EMDASH.findall(b)) for b in experience)),
        "cliches": len(cliches(text)),
        "numeric_bullets": sum(1 for b in experience if re.search(r"\d", b)),
        "placeholder": int(bool(re.search(r"<<[A-Z_]+>>|\[FILLED BY", html))),
    }


def violations(report: dict) -> int:
    """One number for the mechanical rules, so a regression is visible at a glance."""
    return (report["first_person"] + report["emdash_violations"] + report["cliches"]
            + report["placeholder"] * 100)
