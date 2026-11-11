"""What a reviewer would send back about a written CV or cover letter, found by code before anyone reads it.

CLAUDE.md §0, rules before AI: each fault here is one the prompt tells the model to avoid and a pattern can
see, so code finds it and the model is told exactly what to fix, once (`build.feedback`). They come from
review: the owner's own notes of what got caught every time (`libs/scaffolds/tailor_cv.md`,
`tailor_cover.md`, `libs/prompts/03-writing-style.md`, kept for that reason), and a reading of the first
twelve CVs `write_cv.md` wrote (2026-11-11). What only judgement can see, such as whether the right work
leads, is the prompt's own final check.

The word rules are English rules, so they run on English documents only: a German "dynamisch" is not
the cliché the list means, and a Hungarian CV has no "I" to count.
"""
from __future__ import annotations

import re
from html import unescape

from libs.corpus.langfilter import detect_language

# Words that describe nothing, and the AI tells a recruiter skims past. One list for both documents; the
# letter adds its own openings and closings below.
CLICHES = ("results-driven", "passionate", "dynamic", "motivated", "hard-working", "team player",
           "detail-oriented", "go-getter", "proven track record", "leverage", "leveraging", "leveraged",
           "spearheaded", "synergy", "synergies", "cutting-edge", "seamless", "seamlessly", "robust",
           "innovative", "best-in-class", "proactive", "ambitious", "institutional-quality", "delve")
# "End-to-end" is a kind of testing, and only filler otherwise.
_END_TO_END = re.compile(r"(?i)\bend-to-end\b(?!\s+(?:test|tests|testing|tested|encrypt\w*)\b)")

# How a cover letter gives itself away in its first and last lines (`tailor_cover.md`, failures 1 and 7):
# every close about applying rather than the work ("I would welcome a conversation", 2026-11-11).
LETTER_PHRASES = ("i am writing to", "i'm writing to", "i\u2019m writing to", "i am excited to",
                  "i believe i would be a great fit", "i look forward to", "thank you for your consideration",
                  "i would welcome", "i would like to discuss", "how i can contribute",
                  "how i could contribute", "thrilled", "the jd", "job description",
                  "as stated in the posting")

# A bullet that ends by saying what it shows (`tailor_cv.md`, failure 2).
_TELLING_TAIL = re.compile(r"(?i),\s*(?:demonstrating|showcasing|highlighting|underscoring|proving|"
                           r"reflecting)\b[^,.;]*[.]?$")
# A bullet that describes a task and not what came of it (`context.md`, the bullet formula).
_TASK_OPENING = re.compile(r"(?i)^(?:responsible for|worked on|helped|assisted|participated in|"
                           r"was involved in|involved in|duties included)\b")
# A profile about the application rather than the person, or rating itself.
_ABOUT_APPLYING = re.compile(r"(?i)\b(?:applicant|applying|candidate)\b")
# A CV names nobody: one called its owner "he" from their first name (2026-11-11).
_THIRD_PERSON = re.compile(r"\b(?:he|she|him|his|her|hers)\b", re.IGNORECASE)
# A length of experience the model worked out itself ("over two years"), which their documents never said.
_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
_YEARS_SAID = re.compile(rf"(?i)\b(\d+|{'|'.join(_WORDS)})\+?\s+years?\b")
# A role still held, by how its dates end: "Mar 2021 – Present", "2019 – heute", "2020 – jelenleg".
_ONGOING = re.compile(r"(?i)\b(?:present|current|now|today|heute|aktuell|pr[ée]sent|aujourd|actualidad|"
                      r"presente|attuale|jelenleg|napjainkig|obecnie|heden|nu)\b")
_DATES = re.compile(r'(?is)<div class="entry">.*?(?:<span class="entry-date">(.*?)</span>|</div>)')
_SELF_RATING = re.compile(r"(?i)\b(?:strong in|strengths in|excellent|exceptional|outstanding|"
                          r"highly skilled)\b")

# First person. Bare "I" is the hard one: "I/O" and a title's numeral ("Design Engineer I") are not the
# pronoun, and counting them reported first person in CVs that had none (the CV bench, 2026-07).
_Q = "'\u2019"  # an apostrophe, typed or curly
_POSSESSIVE = re.compile(rf"(?<![\w{_Q}])(?:I[{_Q}](?:m|ve|d|ll)|my|me|mine)(?![\w{_Q}])", re.IGNORECASE)
_BARE_I = re.compile(rf"(?<![\w{_Q}])I(?![\w{_Q}/])")
_TITLE_NUMERAL = re.compile(r"[A-Z][a-z]+\s+$")

# Contractions in an English letter (`tailor_cover.md`, failure 5). A possessive "'s" is not one.
_CONTRACTION = re.compile(rf"(?i)\b(?:\w+n[{_Q}]t|i[{_Q}]m|\w+[{_Q}](?:re|ve|ll|d)|"
                          rf"(?:it|that|there|here|what|let|who)[{_Q}]s)\b")
# A colon in a sentence, the claim-colon-elaboration habit (`tailor_cover.md`, failure 4).
_PROSE_COLON = re.compile(r"[^\W\d_]\s*:\s+\S")

# One bullet, one or two lines of A4.
MAX_BULLET_WORDS = 35
# A letter that reads in under a minute, and still says something (`tailor_cover.md`: about 200-260).
LETTER_WORDS = (180, 320)
# A paragraph a reader skims in one look.
MAX_PARAGRAPH_SENTENCES = 4

_TAG = re.compile(r"<[^>]+>")
_LI = re.compile(r"(?is)<li\b[^>]*>(.*?)</li>")
_PROFILE = re.compile(r'(?is)<p class="profile">(.*?)</p>')
_SKILLS = re.compile(r'(?is)<div class="section competencies">.*?</ul>')
_PLAIN = re.compile(r'(?is)<ul class="plain-list">.*?</ul>')
_LETTER = re.compile(r'(?is)<div class="letter">(.*?)</div>')
_PARAGRAPH = re.compile(r'(?is)<p(?: class="([^"]*)")?>(.*?)</p>')
_STOP = re.compile(r"[.!?](?:\s|$)")
# A company as people write it in a sentence: "Acme", not "Acme Ltd".
_LEGAL_FORM = re.compile(r"(?i)[\s,]+(?:ltd\.?|limited|inc\.?|llc|plc|gmbh|ag|b\.?v\.?|n\.?v\.?|s\.?a\.?|"
                         r"kft\.?|zrt\.?|s\.?r\.?l\.?|oy|ab|aps)$")


def text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())


def is_english(html: str) -> bool:
    """Whether the document is in English. Unknown (too short to tell) counts as English: the checks it
    gates are the prompt's own rules, and a wrong "yes" costs one revision, a wrong "no" a missed fault."""
    return detect_language(text(html)) in ("en", None)


def first_person(prose: str) -> int:
    count = len(_POSSESSIVE.findall(prose))
    for match in _BARE_I.finditer(prose):
        if not _TITLE_NUMERAL.search(prose[:match.start()]):
            count += 1
    return count


def cliches(prose: str) -> list[str]:
    lowered = prose.lower()
    found = [word for word in CLICHES if re.search(rf"\b{re.escape(word)}\b", lowered)]
    return found + (["end-to-end"] if _END_TO_END.search(prose) else [])


def bullets(body: str) -> list[str]:
    """The experience bullets: every list item outside Skills and the plain lists."""
    rest = _PLAIN.sub("", _SKILLS.sub("", body))
    return [text(item) for item in _LI.findall(rest)]


def cv(body: str, sources: str = "") -> list[str]:
    """What is wrong with a CV, each as the instruction that fixes it. `sources` are their documents and
    the posting, where a stated length of experience must come from."""
    items = bullets(body)
    found = [*_order(body), *_long(items)]
    if is_english(body):
        found += [*_words_of(text(_SKILLS.sub("", body))), *_profile(text(" ".join(_PROFILE.findall(body))),
                                                                     sources), *_bullets(items)]
    return found


def _order(body: str) -> list[str]:
    """Within each section, every role still held above every role that ended (`write_cv.md`)."""
    for section in body.split('<div class="section-title">')[1:]:
        ongoing = [bool(_ONGOING.search(text(dates))) for dates in _DATES.findall(section) if dates]
        if False in ongoing and True in ongoing[ongoing.index(False):]:
            return ["A role that has ended sits above one they still hold. Put every role they still hold "
                    "first."]
    return []


def _long(items: list[str]) -> list[str]:
    if long := [item for item in items if len(item.split()) > MAX_BULLET_WORDS]:
        return [f"{len(long)} bullet(s) run over {MAX_BULLET_WORDS} words, starting {_quote(long[0])}. Keep "
                "each to one or two lines."]
    return []


def _words_of(prose: str) -> list[str]:
    found = [_said(words)] if (words := cliches(prose)) else []
    if first_person(prose):
        found.append('It says "I", "my" or "me". A CV never does.')
    if match := _THIRD_PERSON.search(prose):
        found.append(f'It calls them "{match.group(0)}". A CV uses no pronouns for the person.')
    return found


def _profile(summary: str, sources: str) -> list[str]:
    found = []
    stated = {_number(each) for each in _YEARS_SAID.findall(sources)}
    if worked_out := [each for each in _YEARS_SAID.findall(summary) if _number(each) not in stated]:
        found.append(f'The profile says "{worked_out[0]} years", which their documents do not. Leave the '
                     "length of their experience to the dates.")
    if match := _ABOUT_APPLYING.search(summary):
        found.append(f'The profile says "{match.group(0)}". It describes the person, not the application.')
    if match := _SELF_RATING.search(summary):
        found.append(f'The profile rates them ("{match.group(0)}"). Show it with their work instead.')
    return found


def _bullets(items: list[str]) -> list[str]:
    found = []
    openers = [item.split()[0].lower() for item in items if item.split()]
    if repeated := sorted({word for word in openers if openers.count(word) > 1}):
        found.append(f"More than one bullet starts with {', '.join(repr(w) for w in repeated)}. Start each "
                     "with a different verb.")
    if tails := [item for item in items if _TELLING_TAIL.search(item)]:
        found.append(f"{_quote(tails[0])} ends by saying what it shows. End on what came of the work.")
    if tasks := [item for item in items if _TASK_OPENING.search(item)]:
        found.append(f"{_quote(tasks[0])} describes a task. Say what they did and what came of it.")
    return found


def letter(body: str, *, company: str, posting: str = "") -> list[str]:
    """What is wrong with a cover letter, each as the instruction that fixes it. A word the posting uses is
    theirs to quote ("a proactive assistant" was A1's own product), so it is not counted as a cliché."""
    found = []
    inside = (_LETTER.search(body) or re.match("(?s)(.*)", body)).group(1)
    paragraphs = [(kind or "", text(part)) for kind, part in _PARAGRAPH.findall(inside)]
    # Every paragraph after the greeting, up to the sign-off.
    prose = [part for kind, part in paragraphs if kind not in ("closing", "signature")][1:]
    words = sum(len(part.split()) for part in prose)
    if not LETTER_WORDS[0] <= words <= LETTER_WORDS[1]:
        found.append(f"The letter is {words} words. Make it {LETTER_WORDS[0]} to {LETTER_WORDS[1]}.")
    if dense := [part for part in prose if len(_STOP.findall(part)) > MAX_PARAGRAPH_SENTENCES]:
        found.append(f"A paragraph runs over {MAX_PARAGRAPH_SENTENCES} sentences, starting "
                     f"{_quote(dense[0])}. Split or cut it.")
    if colons := [part for part in prose if _PROSE_COLON.search(part)]:
        found.append(f"{_quote(colons[0])} has a colon in a sentence. Rewrite it as one plain sentence, "
                     "or two.")
    name = _LEGAL_FORM.sub("", company).strip()
    if name and prose and name.lower() not in prose[0].lower():
        found.append(f"The opening paragraph does not name {name}. Name the company and the role in it.")
    if not is_english(body):
        return found
    flat = " ".join(prose)
    if words_ := [word for word in cliches(flat) if word not in cliches(posting)]:
        found.append(_said(words_))
    if phrases := [p for p in LETTER_PHRASES if p in flat.lower()]:
        found.append(f"It says {', '.join(repr(p) for p in phrases)}, which could end anybody's letter. Say "
                     "something only they could.")
    if contractions := sorted({m.group(0) for m in _CONTRACTION.finditer(flat)}):
        found.append(f"It uses contractions ({', '.join(contractions)}). Write them out in full.")
    return found


def _number(word: str) -> str:
    return str(_WORDS.index(word.lower()) + 1) if word.lower() in _WORDS else word


def _said(words: list[str]) -> str:
    return (f"It uses {', '.join(repr(w) for w in words)}, which describe nothing. Say the specific thing "
            "instead.")


def _quote(part: str) -> str:
    words = part.split()
    return f'"{" ".join(words[:8])}{"…" if len(words) > 8 else ""}"'
