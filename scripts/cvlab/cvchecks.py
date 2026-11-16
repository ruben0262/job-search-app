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
from html import unescape

# The docgen worker's own detections (`services/docgen/checks.py`), so the bench counts what production
# sends back. First person there skips "I/O" and a title's numeral ("Design Engineer I"), which counted as
# the pronoun here once and handed a draft a win it did not earn.
from services.docgen.checks import STILL_HELD, cliches, first_person, is_english

# The em-dash rule the prompt states: banned in bullets and the profile paragraph, allowed in an
# entry's title header. Measured only where it is banned.
_EMDASH = re.compile(r"—|&mdash;|&#8212;")

_LI = re.compile(r"(?is)<li\b[^>]*>(.*?)</li>")
_PROFILE = re.compile(r'(?is)<p\b[^>]*class="[^"]*profile[^"]*"[^>]*>(.*?)</p>')
_ENTRY = re.compile(r'(?is)class="entry"')
_TAGS = re.compile(r"(?s)<[^>]+>")
_STYLE = re.compile(r"(?is)<style\b.*?</style>")

# A bullet that describes a task and not what came of it (`context.md`, the bullet formula). The docgen
# worker said these back until 2026-11-14 (owner): 8 of 44 CVs written for the panel, each for a line the
# person's documents say and say no more about. Counted here, so a wording that writes more of them shows.
_TASK_OPENING = re.compile(r"(?i)^(?:responsible for|worked on|helped|assisted|participated in|"
                           r"was involved in|involved in|duties included)\b")

# Voice and tense, read off the first word of each experience bullet (2026-11-14). Three of twelve CVs
# the live prompt wrote gave a job the person still holds as "Leads", "Manages": found by reading them,
# and counted here on every run instead. English only, as the word rules in `checks` are.
_SECTION = re.compile(r'(?is)<div class="section-title">(.*?)</div>(.*?)(?=<div class="section-title">|$)')
_WORK = re.compile(r"(?i)experience|employment|work|career")
_ENTRY_BLOCK = re.compile(r'(?is)<div class="entry">(.*?)(?=<div class="entry">|$)')
_DATE = re.compile(r'(?is)<span class="entry-date">(.*?)</span>')
_FIRST_WORD = re.compile(r"[A-Za-z]+")
_IRREGULAR_PAST = frozenset({
    "led", "built", "ran", "wrote", "made", "took", "kept", "held", "taught", "won", "grew", "drove",
    "brought", "sold", "sent", "spent", "met", "found", "got", "gave", "began", "oversaw", "rebuilt",
    "rewrote", "dealt", "came", "went", "did", "drew", "chose", "spoke", "stood", "understood",
    "undertook", "bought", "caught", "fed", "felt", "fought", "left", "lent", "lost", "meant", "paid",
    "said", "sought", "told", "thought", "laid", "rose", "saw", "struck", "swept", "threw", "wore",
    "was", "were", "had"})
# The same word in every tense, so it says nothing; and words ending in "s" or "ed" that are neither.
_EITHER = frozenset({
    "cut", "set", "put", "read", "shut", "let", "hit", "spread", "cast", "cost", "beat", "bid", "split",
    "quit", "shed"})
_NOT_THIRD = frozenset({"across", "always", "as", "its", "plus", "sales", "operations"})
_NOT_PAST = frozenset({"need", "speed", "feed", "embed", "exceed", "proceed", "succeed"})


def tense(word: str) -> str | None:
    """How a bullet's first word reads: "third" (Manages), "past" (Managed), "present" (Manage), or None
    for a word that is the same in every tense."""
    word = word.lower()
    if word in _EITHER:
        return None
    if word in _IRREGULAR_PAST or (word.endswith("ed") and word not in _NOT_PAST):
        return "past"
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and word not in _NOT_THIRD:
        return "third"
    return "present"


def voice(html: str) -> dict:
    """Bullets under a job that start in the third person ("Manages"), in the present under a job that
    ended, or in the past under a job still held, and sentences of the profile that start in the third
    person ("Runs the pass two days a week", in 6 of 13 CVs reviewed on 2026-11-14, over bullets that say
    "Run"). Counted, not judged: finished work in a job still held is rightly in the past. None for a CV
    that is not in English, so it is left out of a mean rather than counted as clean."""
    counts = {"third_person": 0, "profile_third_person": 0, "present_in_ended": 0, "past_in_held": 0}
    if not is_english(html.split("<body>", 1)[-1]):
        return dict.fromkeys(counts)
    for sentence in re.split(r"(?<=[.!?])\s+", body_parts(html)[1]):
        first = _FIRST_WORD.match(sentence)
        counts["profile_third_person"] += bool(first) and tense(first.group(0)) == "third"
    for title, section in _SECTION.findall(html):
        if not _WORK.search(_TAGS.sub("", title)):
            continue
        for entry in _ENTRY_BLOCK.findall(section):
            dates = _DATE.search(entry)
            held = bool(STILL_HELD.search(_TAGS.sub(" ", dates.group(1)))) if dates else None
            for bullet in _LI.findall(entry):
                first = _FIRST_WORD.search(_TAGS.sub("", bullet))
                how = tense(first.group(0)) if first else None
                counts["third_person"] += how == "third"
                counts["present_in_ended"] += held is False and how in ("present", "third")
                counts["past_in_held"] += held is True and how == "past"
    return counts


# A2 at 10pt in this layout runs about 3,600 characters of extracted text per page. Only ever used
# as a fitting signal — the real page count needs Chrome, which the bench does not have.
CHARS_PER_PAGE = 3_600


def text_of(html: str) -> str:
    """The document as a reader sees it: no style block, no tags, "&" for "&amp;", whitespace collapsed."""
    return re.sub(r"\s+", " ", unescape(_TAGS.sub(" ", _STYLE.sub(" ", html)))).strip()


def body_parts(html: str) -> tuple[list[str], str]:
    """The bullets and the profile paragraph — the two places most rules apply."""
    def plain(part: str) -> str:
        return re.sub(r"\s+", " ", unescape(_TAGS.sub("", part))).strip()

    profile = _PROFILE.search(html)
    return [plain(part) for part in _LI.findall(html)], plain(profile.group(1)) if profile else ""


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
        "task_bullets": sum(1 for b in experience if _TASK_OPENING.search(b)),
        "placeholder": int(bool(re.search(r"<<[A-Z_]+>>|\[FILLED BY", html))),
        "said_twice": said_twice(profile, competency, experience),
        **voice(html),
    }


def said_twice(profile: str, skills: list[str], bullets: list[str]) -> int:
    """How much of a CV is said twice in the same words: the runs of three words that stand in two of
    its profile, its skills (without their headings) and its bullets. A profile that gives a bullet's fact
    in its own words counts nothing; one that copies the bullet counts every run in it."""
    def runs(text: str) -> set[str]:
        words = re.findall(r"[^\W_]+(?:[.,]\d+)?%?", text.casefold())
        return {" ".join(words[at:at + 3]) for at in range(len(words) - 2)}

    in_profile = runs(profile)
    in_skills = set().union(*(runs(line.split(":", 1)[1]) for line in skills))
    in_bullets = set().union(*(runs(bullet) for bullet in bullets))
    return len(in_profile & in_bullets) + len(in_skills & in_bullets) + len(in_profile & in_skills)


def violations(report: dict) -> int:
    """One number for the mechanical rules, so a regression is visible at a glance."""
    return (report["first_person"] + report["emdash_violations"] + report["cliches"]
            + report["placeholder"] * 100)
