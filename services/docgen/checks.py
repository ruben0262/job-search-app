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

import itertools
import re
from html import unescape

from libs.corpus.langfilter import detect_language

# Words that describe nothing, and the AI tells a recruiter skims past. One list for both documents; the
# letter adds its own openings and closings below.
CLICHES = ("results-driven", "passionate", "motivated", "hard-working", "team player",
           "detail-oriented", "go-getter", "proven track record", "leverage", "leveraging", "leveraged",
           "spearheaded", "synergy", "synergies", "cutting-edge", "seamless", "seamlessly", "robust",
           "innovative", "best-in-class", "proactive", "ambitious", "institutional-quality", "delve",
           "delves", "delved", "delving")
# "End-to-end" is a kind of testing, and only filler otherwise.
_END_TO_END = re.compile(r"(?i)\bend-to-end\b(?!\s+(?:test|tests|testing|tested|encrypt\w*)\b)")
# "Dynamic" is a kind of advert and of pricing: a marketer's "dynamic remarketing" was sent back as filler
# in 2 of her 4 CVs (2026-11-14).
_DYNAMIC = re.compile(r"(?i)\bdynamic\b(?!\s+(?:remarketing|retargeting|pricing|ads?|creatives?|content)\b)")

# How a cover letter gives itself away in its first and last lines (`tailor_cover.md`, failures 1 and 7):
# every close about applying rather than the work ("I would welcome a conversation", 2026-11-11).
LETTER_PHRASES = ("i am writing to", "i'm writing to", "i\u2019m writing to", "i am excited to",
                  "i believe i would be a great fit", "i look forward to", "thank you for your consideration",
                  "i would welcome", "i would like to discuss", "how i can contribute",
                  "how i could contribute", "thrilled", "the jd", "job description",
                  "as stated in the posting")

# What reads as written by AI (owner, 2026-11-19: "make sure it passes without using any AI language"): the
# turns current models write many times as often as people do, by what was measured (CHANGELOG 2026-11-19,
# night: Graphite's per-model rates over 100,000 articles, Kobak et al. 2025, Pangram, the Antislop paper,
# and what recruiters quote from AI letters). Only those a person almost never writes in a letter, so one
# sends it back: the words models over-use that people use too ("practical", "steady", "rather than",
# "crucial") are left out, a letter being too short to tell the two apart. Most are Gemini's, which keeps
# the 2023 vocabulary at 8 to 26 times a person's rate; the frames are GPT's and Claude's. Tells change
# with each model, so the list is measured again when a provider's model changes.
AI_LANGUAGE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    # A chatbot's own words, a placeholder or markdown left in.
    r"\bhere(?: is|[\u2019']s) (?:a|an|the|your) (?:\w+ )?(?:cover letter|letter|draft|version)\b"
    r"|\bI hope this helps\b|\blet me know if you\b|\bas an AI (?:language model|model|assistant)\b",
    r"\[[^\]\n]{0,30}\b(?:name|company|position|role|title|date|address|insert|manager|employer)\b"
    r"[^\]\n]{0,30}\]|\{\{[^}\n]{1,40}\}\}",
    r"\*\*[^*\n]{1,80}\*\*",
    # The vocabulary (Gemini at 8-26 times a person; Kobak's rarest excess words; recruiters' AI letters).
    r"\btestament to\b|\bunwavering\b|\bunderscor(?:e|es|ed|ing)\b|\b(?:in|within|into) the realms? of\b",
    r"\b(?:rich|vibrant|complex|intricate|diverse) tapestr(?:y|ies)\b|\btapestr(?:y|ies) of\b",
    r"\b(?:ever[- ]?(?:evolving|changing)|evolving|dynamic|shifting) (?:\w+ )?landscape\b"
    r"|\bin today[\u2019']s (?:[\w-]+ ){0,2}(?:world|age|era|landscape|society)\b",
    r"\bnavigat(?:e|es|ed|ing) (?:the |these |its |their )?"
    r"(?:complexit(?:y|ies)|intricac(?:y|ies)|nuances?)\b",
    r"\b(?:commendable|elucidat(?:e|es|ed|ing)|burgeoning|multifaceted|interplay|unparalleled|uncharted|"
    r"noteworthy|bolster(?:s|ed|ing)?|garner(?:s|ed|ing)?)\b",
    r"\b(?:it is|it[\u2019']s) (?:important|crucial|essential|vital|worth) "
    r"(?:to note|noting|to mention|to highlight)\b",
    r"\bnatural flair\b|\binsatiable curiosity\b|\bharness(?:es|ed|ing)? (?:the )?(?:power|potential)\b",
    r"\balign(?:s|ed)? (?:perfectly|seamlessly|precisely|exactly|beautifully) with\b|\bperfect(?:ly)? align",
    r"\bserv(?:es|ed) as (?:a|an) (?:(?:powerful|poignant|constant|stark) )?(?:testament|reminder|beacon|"
    r"cornerstone)\b|\bst(?:ands|ood) as (?:a|an) (?:testament|reminder|beacon)\b",
    r"(?:^|[.!?] )(?:in conclusion|in summary|to sum up)\b",
    # What recruiters quote from AI letters.
    r"\bresonat(?:e|es|ed) (?:deeply |strongly )?with me\b|\bembark(?:ing)? on\b"
    r"|\b(?:professional|career) journey\b|\bunique blend of\b",
    # The frames (GPT 74-576 times a person; Claude 25-120): "not simply", "less like ... more like", "is not
    # a job; it is", "This matters".
    r"\bnot simply\b|\brather than (?:simply|merely)\b|\bdoes not (?:by itself |alone )?establish\b",
    r"\bless (?:like )?(?:a|an|about) [^.!?;]{1,40}?(?: and|,) more (?:like )?(?:a|an|about)\b",
    r"(?:\b(?:is|was) not|[\u2019']s not|\b(?:isn|wasn)[\u2019']t|\b(?:is|was) more than) "
    r"(?:just |simply |merely )?(?:a|an) (?:(?!\bI\b)[^.!?;]){1,40}?[,;:.]\s*(?:it|this|that)"
    r"(?:[\u2019']s| is| was)\b",
    r"(?:^|[.!?] )(?:this|that) matters\b(?! to)|\bmatters because\b",
    r"\bload[- ]bearing\b(?! (?:walls?|beams?|columns?|structures?|elements?|capacity|partitions?|masonry|"
    r"steel|timber|frames?|points?))|\bearns? (?:its|their) keep\b|\bthe honest (?:answer|truth|take)\b",
))

# A bullet that ends by saying what it shows (`tailor_cv.md`, failure 2).
_TELLING_TAIL = re.compile(r"(?i),\s*(?:demonstrating|showcasing|highlighting|underscoring|proving|"
                           r"reflecting)\b[^,.;]*[.]?$")
# A profile about the application rather than the person, or rating itself.
_ABOUT_APPLYING = re.compile(r"(?i)\b(?:applicant|applying|candidate)\b")
# A CV names nobody: one called its owner "he" from their first name (2026-11-11).
_THIRD_PERSON = re.compile(r"\b(?:he|she|him|his|her|hers)\b", re.IGNORECASE)
# A length of experience the model worked out itself ("over two years"), which their documents never said.
_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
_YEARS_SAID = re.compile(rf"(?i)\b(\d+|{'|'.join(_WORDS)})\+?\s+years?\b")
# A role still held, by how its dates end: "Mar 2021 – Present", "2019 – heute", "2020 – jelenleg".
STILL_HELD = re.compile(r"(?i)\b(?:present|current|now|today|since|heute|aktuell|seit|pr[ée]sent|aujourd|"
                        r"depuis|actualidad|presente|desde|attuale|dal|jelenleg|napjainkig|obecnie|heden|nu)\b")
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

# The headings of the prompt's own markup example. A CV written in German came out headed "Skills",
# "Experience", "Education" and "Languages" (1 of the 8 written in German and Hungarian, 2026-11-14).
_EXAMPLE_HEADINGS = frozenset({"skills", "experience", "education", "languages"})
_SECTION_TITLE = re.compile(r'(?is)<div class="section-title">(.*?)</div>')

# One bullet, one or two lines of A4.
MAX_BULLET_WORDS = 35
# A letter that reads in under a minute, and still says something (`tailor_cover.md`: about 200-260).
LETTER_WORDS = (180, 320)
# An English word and its space, in the letters written for the panel (5.6 to 5.9).
CHARS_A_WORD = 6
# A paragraph a reader skims in one look.
MAX_PARAGRAPH_SENTENCES = 4
# The longest greeting: "Sehr geehrte Frau Dr. Baron," and "Dear Hiring Manager," are five and three.
GREETING_WORDS = 8

_TAG = re.compile(r"<[^>]+>")
_LI = re.compile(r"(?is)<li\b[^>]*>(.*?)</li>")
_PROFILE = re.compile(r'(?is)<p class="profile">(.*?)</p>')
_SKILLS = re.compile(r'(?is)<div class="section competencies">.*?</ul>')
_PLAIN = re.compile(r'(?is)<ul class="plain-list">.*?</ul>')
_LETTER = re.compile(r'(?is)<div class="letter">(.*?)</div>')
_PARAGRAPH = re.compile(r'(?is)<p(?: class="([^"]*)")?>(.*?)</p>')
_STOP = re.compile(r"[.!?](?:\s|$)")
# Where a paragraph may be cut: a sentence's end, not a title's full stop ("Dr. Baron", "St. Albans").
_PARAGRAPH_CUT = re.compile(r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bSt)(?<!\bNo)(?<!\bProf)[.!?]\s+")
_PLAIN_PARAGRAPH = re.compile(r"(?is)<p>(.*?)</p>")
# A company as people write it in a sentence: "Acme", not "Acme Ltd", "Bosch Group" or "J. RETTENMAIER &
# SÖHNE GmbH + Co KG", and none of a strapline after a bar ("VNOM | We get the job done").
_LEGAL_FORM = re.compile(r"(?i)(?:[\s,]+(?:ltd\.?|limited|inc\.?|llc|plc|gmbh|ag|se|kg|co\.?|b\.?v\.?|"
                         r"n\.?v\.?|s\.?a\.?|s\.?p\.?a\.?|kft\.?|zrt\.?|nyrt\.?|s\.?r\.?l\.?|oy|ab|aps|group|"
                         r"holdings?|[&+]))+$")
_STRAPLINE = re.compile(r"\s+\|.*$")
# A word of a company's name that is a name of its own: four letters or more, not an initial.
_LETTER_OF = re.compile(r"[^\W\d_]")


# A word in bold or italics is one word with what touches it: "<strong>Analyst</strong>," is "Analyst,", and
# read as "Analyst ," its comma was counted as a word (a letter now sets its job title in bold, 2026-11-26).
_INLINE = re.compile(r"(?i)</?(?:strong|em|b|i)>")


def text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", _INLINE.sub("", html))).split())


def language(prose: str) -> str:
    """The language a text is in. Unknown (too short to tell) counts as English: the checks it gates are
    the prompt's own rules, and a wrong "yes" costs one revision, a wrong "no" a missed fault."""
    return detect_language(prose) or "en"


def is_english(html: str) -> bool:
    return language(text(html)) == "en"


def first_person(prose: str) -> int:
    count = len(_POSSESSIVE.findall(prose))
    for match in _BARE_I.finditer(prose):
        if not _TITLE_NUMERAL.search(prose[:match.start()]):
            count += 1
    return count


def cliches(prose: str) -> list[str]:
    lowered = prose.lower()
    found = [word for word in CLICHES if re.search(rf"\b{re.escape(word)}\b", lowered)]
    return (found + (["dynamic"] if _DYNAMIC.search(prose) else [])
            + (["end-to-end"] if _END_TO_END.search(prose) else []))


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
    else:
        found += _headings(body)
    return found


def _headings(body: str) -> list[str]:
    """In a CV that is not in English, headings left as the markup example has them."""
    copied = [title for title in map(text, _SECTION_TITLE.findall(body))
              if title.casefold() in _EXAMPLE_HEADINGS]
    if len(copied) > 1:
        return [f"The headings {', '.join(copied)} are in English and the CV is not. Write every heading "
                "in the language of the CV."]
    return []


def _order(body: str) -> list[str]:
    """Within each section, every role still held above every role that ended (`write_cv.md`)."""
    for section in body.split('<div class="section-title">')[1:]:
        ongoing = [bool(STILL_HELD.search(text(dates))) for dates in _DATES.findall(section) if dates]
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
    # A bullet that only describes a task ("Helped the sous chef with orders") is not said back (owner,
    # 2026-11-14). Where their documents say no more, a second call had nothing to add: of four run, all
    # four reworded the line and one made it more than it was. The bench counts them (`cvchecks`).
    return found


def letter(body: str, *, company: str, posting: str = "") -> list[str]:
    """What is wrong with a cover letter, each as the instruction that fixes it. A word the posting uses is
    theirs to quote ("a proactive assistant" was A1's own product), so it is not counted as a cliché."""
    found = []
    prose = _prose(body)
    words = letter_words(body)
    if not LETTER_WORDS[0] <= words <= LETTER_WORDS[1]:
        found.append(f"The letter is {words} words. Make it {LETTER_WORDS[0]} to {LETTER_WORDS[1]}.")
    if dense := [part for part in prose if len(_STOP.findall(part)) > MAX_PARAGRAPH_SENTENCES]:
        found.append(f"A paragraph runs over {MAX_PARAGRAPH_SENTENCES} sentences, starting "
                     f"{_quote(dense[0])}. Split or cut it.")
    if colons := [part for part in prose if _PROSE_COLON.search(part)]:
        found.append(f"{_quote(colons[0])} has a colon in a sentence. Rewrite it as one plain sentence, "
                     "or two.")
    if (name := employer(company)) and prose and not names_employer(prose[0], company, posting):
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
    if said := ai_language(flat, but=posting):
        found.append(f"It says {', '.join(repr(each) for each in said)}, which reads as written by AI. "
                     "Say it the plain way this person would.")
    return found


def ai_language(prose: str, *, but: str = "") -> list[str]:
    """What in `prose` reads as written by AI (`AI_LANGUAGE`), each as written. A turn the posting itself uses
    (`but`) is theirs to quote."""
    return [found.group(0) for each in AI_LANGUAGE if (found := each.search(prose)) and not each.search(but)]


def employer(company: str) -> str:
    """The company as a letter names it in a sentence (`_LEGAL_FORM`, `_STRAPLINE`)."""
    return _LEGAL_FORM.sub("", _STRAPLINE.sub("", unescape(company or ""))).strip(" ,")


def paragraphs_split(body: str) -> str:
    """A letter whose paragraph runs over `MAX_PARAGRAPH_SENTENCES` sentences, with that paragraph as two or
    more of at most three, cut where its sentences end. The words stay as written. It was a second call on the
    person's plan, which rewrote the letter to do it: 3 of the 8 first drafts Claude Haiku 5.5 had sent back
    on the live wording (2026-11-20). A paragraph with markup inside is left to the check."""
    def cut(match: re.Match[str]) -> str:
        inside = match.group(1)
        if "<" in inside or len(_STOP.findall(inside)) <= MAX_PARAGRAPH_SENTENCES:
            return match.group(0)
        starts = [0] + [end.end() for end in _PARAGRAPH_CUT.finditer(inside)
                        if _starts_sentence(inside[end.end():])] + [len(inside)]
        sentences = [inside[a:b].strip() for a, b in itertools.pairwise(starts)]
        parts = -(-len(sentences) // 3)
        if parts < 2:
            return match.group(0)
        size, more = divmod(len(sentences), parts)
        groups, at = [], 0
        for each in range(parts):
            take = size + (each < more)
            groups.append(" ".join(sentences[at:at + take]))
            at += take
        return "".join(f"<p>{group}</p>" for group in groups)

    inside = _LETTER.search(body)
    if not inside:
        return body
    return body[:inside.start(1)] + _PLAIN_PARAGRAPH.sub(cut, inside.group(1)) + body[inside.end(1):]


def _starts_sentence(rest: str) -> bool:
    """A capital comes next, after any opening quote or bracket: "e.g. a forklift" goes on."""
    letter_ = rest.lstrip("\"'\u201c\u2018(")[:1]
    return letter_.isalpha() and letter_.isupper()


def names_employer(text: str, company: str, posting: str = "") -> bool:
    """Whether `text` names the company: as `employer` writes it; by the first word of it that is a name of
    its own, four letters or more with its capital ("Rushcliffe Care Group" for Rushcliffe Care Limited,
    "Bosch Service Solutions" for Bosch Group, "1KOMMA5°"), or two or three capitals as written ("AMC" of AMC
    Global Media); or by its initials where its posting goes by them ("JRS", J. Rettenmaier & Söhne). A
    board's field is often not what a person writes: letters naming their employer as the posting does were
    sent back for not naming it, 6 of the 8 so sent back of 36 (2026-11-18), and three more the next day. "a
    person who" is not Person Centred Software. The CV bench holds letters to this too."""
    def words(part: str) -> str:
        return f" {' '.join(re.findall(r'[^\W_]+', part.casefold()))} "

    def said(token: str, where: str, *, exact: bool) -> list[str]:
        return [found.group(0) for found in re.finditer(rf"(?<![^\W_]){re.escape(token)}(?![^\W_])", where,
                                                         0 if exact else re.IGNORECASE)]

    name = employer(company)
    if not name:
        return False
    if words(name) in words(text):
        return True
    tokens = name.split()
    own = next((token for token in tokens
                if len(_LETTER_OF.findall(token)) >= 4 or re.fullmatch(r"[A-Z]{2,3}", token)), None)
    # A name the company itself begins in lower case is named as it writes it: "emagine" of emagine Polska,
    # "dmTECH", "j-labs", 6.2% of postings (2026-11-21). Only its first word: "inclusive" in "an inclusive
    # team" is not All inclusive Fitness.
    if own and any(_LETTER_OF.search(found).group(0).isupper() or (found == own and own == tokens[0])
                   for found in said(own, text, exact=bool(re.fullmatch(r"[A-Z]{2,3}", own)))):
        return True
    initials = "".join(token[0] for token in tokens if _LETTER_OF.match(token)).upper()
    return len(initials) >= 3 and all(said(initials, where, exact=True) for where in (posting, text))


def _prose(body: str) -> list[str]:
    """A letter's paragraphs after the greeting and before its sign-off, as text. The sign-off is the last
    paragraph marked `closing`: a model once marked its closing paragraph so too, and the check skipped it,
    the words in it and whatever it said (1 of 54 letters, 2026-11-18)."""
    inside = (_LETTER.search(body) or re.match("(?s)(.*)", body)).group(1)
    paragraphs = [(kind or "", text(part)) for kind, part in _PARAGRAPH.findall(inside)]
    end = max((at for at, (kind, _) in enumerate(paragraphs) if kind == "closing"), default=len(paragraphs))
    # The greeting is a few words not ended as a sentence ("Dear Ms Baron,"). A letter that began without
    # one had its opening skipped by every check, and was sent back for not naming the employer it named
    # there (1 of 144 letters, 2026-11-20).
    first = paragraphs[0][1].rstrip() if paragraphs else ""
    start = 1 if len(first.split()) <= GREETING_WORDS and not first.endswith((".", "?")) else 0
    return [part for kind, part in paragraphs[start:end] if kind != "signature"]


def letter_words(body: str) -> int:
    """How long a letter is: its words from the greeting to the sign-off, neither counted, as the prompt
    counts them. In another language the same letter is fewer, longer words (in Hungarian 166 where English
    has about 230, at the same 1,300 characters), and 2 of 4 were sent back as too short (2026-11-14): there
    it is measured at six characters a word. The CV bench's length is this one too."""
    prose = _prose(body)
    return (sum(len(part.split()) for part in prose) if is_english(body)
            else round(sum(len(part) for part in prose) / CHARS_A_WORD))


def _number(word: str) -> str:
    return str(_WORDS.index(word.lower()) + 1) if word.lower() in _WORDS else word


def _said(words: list[str]) -> str:
    return (f"It uses {', '.join(repr(w) for w in words)}, which describe nothing. Say the specific thing "
            "instead.")


def _quote(part: str) -> str:
    words = part.split()
    return f'"{" ".join(words[:8])}{"…" if len(words) > 8 else ""}"'
