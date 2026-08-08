"""What a cover letter did, measured rather than judged.

Every check here is a search-and-count over the finished letter, so two runs of the same letter
give the same answer and a prompt change can be argued with a number. Nothing subjective lives
here: whether a letter is *warm*, or whether its anecdote is believable, is a question for a
reader (or a judge model), not for a regular expression.

The split matters because of what the letters actually do. Measured on the Cloudbeds letter of
2026-06-22: **1 mechanical violation and 12 judgement violations**. Every rule the live prompt
phrased as a literal pass over the text was kept (zero colons, zero em-dashes, zero contractions,
a real figure present, 250 words inside the band); the one mechanical rule it missed is the only
one of the five that is not literal, since it asks where a phrase sits across sentences and
accepts the title's "closest honest equivalent". Everything asking the model to weigh the whole
draft was broken, five times over in the case of "show, do not tell".

So the rules worth turning into code are the second kind, and this module is where that
conversion is measured before any prompt is touched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── the shape of the letter ───────────────────────────────────────────────────────────────────

_NOT_PROSE = re.compile(r"(?is)<(style|script)\b.*?</\1>")
_TAG = re.compile(r"<[^>]+>")

# A letter's prose lives in `<p>`. Those are matched on their own rather than as part of a general
# block pattern, because the model reliably wraps the body in a container:
#
#     <div class="profile"> <p>Dear …</p> <p>At FlowSolutions, …</p> </div>
#
# A non-greedy `<div>…</div>` swallows every one of those paragraphs as a single block, and since
# the greeting is then the first thing *inside* that block, everything after the greeting gets
# skipped as signature. Measured on the first real run: 7 of 8 letters collapsed to one paragraph
# and one of them to nine words. Paragraphs first, containers only as a fallback.
_PARA = re.compile(r"(?is)<p\b[^>]*>(.*?)</p>")
_BLOCK = re.compile(r"(?is)<(div|h[1-6]|li)\b[^>]*>(.*?)</\1>")

# Where the letter proper starts and stops. Anything outside is header, date or signature, and
# none of the prose rules are about those.
_GREETING = re.compile(r"(?i)^\s*(dear\b|to whom\b|hello\b|tisztelt\b|sehr geehrte|madame,|monsieur,)")
_SIGNOFF = re.compile(r"(?i)^\s*(kind regards|best regards|yours (sincerely|faithfully)|sincerely|"
                      r"regards|tisztelettel|mit freundlichen|cordialement|met vriendelijke)")

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", fragment)).replace("&amp;", "&").strip()


def _blank_line_blocks(document: str) -> list[str]:
    """Paragraphs from a document that marks them with blank lines instead of tags.

    The last resort, and it is needed: one letter came back with the whole body as bare text
    inside a single `<div class="profile">`, greeting included and not a `<p>` in the file. Both
    tag-based passes then see one block that starts with "Dear …", conclude the letter is all
    signature, and return nothing — which scores as a zero-word letter rather than as a letter
    the parser could not read.
    """
    text = re.sub(r"(?i)<br\s*/?>", "\n", document)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n\n", text)
    text = _TAG.sub(" ", text).replace("&amp;", "&")
    blocks = [re.sub(r"[^\S\n]+", " ", block).strip()
              for block in re.split(r"\n\s*\n", text)]
    return [block.replace("\n", " ").strip() for block in blocks if block.strip()]


def paragraphs(letter: str) -> list[str]:
    """The prose paragraphs: what sits between the greeting and the sign-off.

    Header, contact line, date and signature are excluded deliberately. The contact line is the
    one place a colon is allowed, and the signature is the one place the candidate's name is not
    a repetition, so folding them into the body would make two checks lie.
    """
    stripped = _NOT_PROSE.sub(" ", letter)
    for candidate in (_paragraph_tags(stripped), _container_tags(stripped),
                      _blank_line_blocks(stripped)):
        found = _between_greeting_and_signoff(candidate)
        if found:
            return found
    return []


def _paragraph_tags(document: str) -> list[str]:
    blocks = [_text(body) for body in _PARA.findall(document)]
    return [block for block in blocks if block] if len(blocks) >= 2 else []


def _container_tags(document: str) -> list[str]:
    return [block for block in (_text(body) for _, body in _BLOCK.findall(document)) if block]


def _between_greeting_and_signoff(blocks: list[str]) -> list[str]:
    """The prose, or nothing if these blocks do not look like a letter."""
    if not blocks:
        return []

    start = next((i + 1 for i, block in enumerate(blocks) if _GREETING.search(block)), None)
    if start is None:
        # No greeting found: fall back to every block with a sentence in it, so a malformed
        # letter is still measured rather than silently scoring zero violations.
        return [block for block in blocks if len(block.split()) >= 8]

    end = next((i for i, block in enumerate(blocks) if i > start and _SIGNOFF.search(block)),
               len(blocks))
    return [block for block in blocks[start:end] if block]


def body(letter: str) -> str:
    """The prose, as one string."""
    return " ".join(paragraphs(letter))


def sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text) if part.strip()]


def words(text: str) -> list[str]:
    """Words as a reader counts them, so `71` and `24/7` are words and not punctuation.

    The curly apostrophe in the pattern is deliberate and is not a typo for the straight one: a
    model writes "Ruben’s" as often as "Ruben's", and both are one word.
    """
    return re.findall(r"[A-Za-zÀ-ɏ0-9][A-Za-zÀ-ɏ0-9'’/-]*", text)  # noqa: RUF001


# ── the findings ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One rule, broken once, with the text that broke it."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


# ── mechanical rules: the ones the model already keeps, kept as a regression ───────────────────

# Both apostrophes and both dashes are matched on purpose — they are the characters being
# detected, so `RUF001` has nothing to warn about here.
#
# `_DASH_CHARS` is not `services.docgen.build._EMDASH` and must not be merged with it. That one
# is a substitution pattern (it eats the surrounding spaces so a dash becomes ", ") and runs on
# the way out to strip em-dashes from a letter. This one only reports, and deliberately also
# catches the en-dash, because a model that answers a ban on "—" by reaching for "–" has not
# obeyed the rule and a reader would see no difference.
_CONTRACTION = re.compile(r"(?i)\b(?:i|you|we|they|he|she|it|that|there|who|what|let|do|does|did|"
                          r"is|are|was|were|have|has|had|would|will|can|could|should|must|ai)"
                          r"[’'](?:s|t|d|ll|ve|re|m)\b")  # noqa: RUF001
_DASH_CHARS = re.compile(r"[—–]|&mdash;|&#8212;")  # noqa: RUF001


def colons(text: str) -> list[Finding]:
    """Every colon in the prose. The contact line is not in the prose, so the target is zero."""
    return [Finding("COLON", _around(text, match.start()))
            for match in re.finditer(r":", text)]


def emdashes(text: str) -> list[Finding]:
    return [Finding("EMDASH", _around(text, match.start()))
            for match in _DASH_CHARS.finditer(text)]


def contractions(text: str) -> list[Finding]:
    return [Finding("CONTRACTION", match.group(0)) for match in _CONTRACTION.finditer(text)]


def no_number(text: str) -> list[Finding]:
    """At least one real figure has to appear, or the letter is claims without evidence."""
    if re.search(r"\d", text):
        return []
    return [Finding("NO_NUMBER", "no digit-bearing figure anywhere in the body")]


def identifies_the_job(text: str, *, company: str, title: str) -> list[Finding]:
    """The company and the role, both inside the first two sentences."""
    opening = " ".join(sentences(text)[:2]).lower()
    out = []
    if company and company.split(maxsplit=1)[0].lower() not in opening:
        out.append(Finding("OPEN_NO_COMPANY", f"{company!r} is not in the first two sentences"))
    if title and not _title_present(opening, title):
        out.append(Finding("OPEN_NO_TITLE", f"{title!r} is not in the first two sentences"))
    return out


def _title_present(opening: str, title: str) -> bool:
    """A title counts as present when its distinctive words are. `Staff Software Engineer` is
    matched by `Staff Engineer`, because the prompt asks for the closest honest equivalent."""
    parts = [word for word in words(title.lower()) if word not in _TITLE_FILLER]
    if not parts:
        return True
    return sum(part in opening for part in parts) >= max(1, len(parts) - 1)


_TITLE_FILLER = {"the", "a", "an", "and", "of", "for", "senior", "junior", "mid", "level", "i",
                "ii", "iii", "remote", "hybrid"}


# ── judgement rules, made mechanical: the ones the model breaks ────────────────────────────────

# Openings that describe the company or the posting back at the reader. Failure 1 and 2 of the
# live prompt, which restate the rule five times between them and still lose.
_RESTATING_OPENER = re.compile(
    r"(?i)^\s*(?:at\s+)?[A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,3}\s+"
    r"(?:builds?|is|are|was|provides?|offers?|seeks?|helps?|powers?|develops?|creates?|"
    r"operates?|delivers?|makes?|runs?|has\b|does\b|enables?|transforms?)\b")
_POSTING_OPENER = re.compile(
    r"(?i)\b(?:is|are)\s+(?:looking|hiring|seeking|searching)\b|\byou\s+(?:are|"
    r"were)\s+(?:looking|hiring|seeking)\b|\bi\s+am\s+writing\s+to\b|"
    r"\bin\s+response\s+to\s+(?:your|the)\s+(?:advert|posting|job)")


def restating_opening(text: str) -> list[Finding]:
    """The first sentence explains the company or the advert instead of showing the work."""
    first = next(iter(sentences(text)), "")
    if not first:
        return []
    if _POSTING_OPENER.search(first):
        return [Finding("OPEN_RESTATES_POSTING", first)]
    if _RESTATING_OPENER.match(first):
        return [Finding("OPEN_DESCRIBES_COMPANY", first)]
    return []


# Telling the reader the experience matches, instead of showing work whose relevance is obvious.
# Every phrase here is one the live prompt bans in prose and the model produced anyway.
_TELLING = [
    r"\b(?:the\s+)?(?:deeper\s+|real\s+|closest\s+|strongest\s+)?(?:technical\s+)?fit\b",
    r"\bsimilar\s+work\b",
    r"\bexactly\s+(?:what|where|the\s+kind)\b",
    r"\bis\s+exactly\b",
    r"\bmatch(?:es|ed|ing)?\s+(?:your|the|what)\b",
    r"\balign(?:s|ed|ing)?\s+(?:with|to)\b",
    r"\bmaps?\s+(?:directly\s+)?(?:on)?to\s+(?:your|the)\b",
    r"\bwhat\s+(?:you|your\s+team)\s+(?:need|are\s+looking\s+for)\b",
    r"\byour\s+requirements?\b",
    r"\b(?:a\s+)?(?:strong|great|perfect|natural|ideal)\s+fit\b",
    r"\bappeals?\s+to\s+me\b",
    r"\bi\s+(?:believe|am\s+confident)\s+i\b",
    r"\bwell[-\s]suited\b",
    r"\bthis\s+is\s+(?:precisely|exactly)\b",
    r"\bdeepest\s+expertise\b",
]
_TELLING_RE = re.compile("|".join(_TELLING), re.I)


def telling_the_match(text: str) -> list[Finding]:
    """Announcing the fit rather than letting it be obvious. The most-repeated rule in the live
    prompt and the most-broken one."""
    return [Finding("TELLS_THE_MATCH", _around(text, match.start()))
            for match in _TELLING_RE.finditer(text)]


_WEAK_CLOSE = re.compile(
    r"(?i)\b(?:would\s+welcome\s+the\s+opportunity|look(?:ing)?\s+forward\s+to\s+hearing|"
    r"thank\s+you\s+for\s+your\s+(?:consideration|time)|discuss\s+how\s+i\s+(?:can|could)|"
    r"hope\s+to\s+hear|would\s+love\s+the\s+chance|at\s+your\s+earliest\s+convenience|"
    r"i\s+would\s+welcome\s+(?:a|the)\s+(?:chance|conversation|opportunity)|"
    r"bring\s+this\s+approach\s+to\s+your\s+team|"
    r"please\s+(?:do\s+not\s+hesitate|feel\s+free)\s+to\s+contact)")


def weak_close(text: str) -> list[Finding]:
    """The last two sentences, against the filler closes the prompt names."""
    tail = " ".join(sentences(text)[-2:])
    match = _WEAK_CLOSE.search(tail)
    return [Finding("WEAK_CLOSE", match.group(0))] if match else []


# The live prompt's own ban list, plus the inflections it produced ("robustly" for "robust").
_BANNED = [
    "institutional-quality", "end-to-end", "leverag", "robust", "passionate", "results-driven",
    "i am writing to express", "dynamic", "innovative", "cutting-edge", "best-in-class",
    "i believe i would be a great fit", "synerg", "world-class", "seamless", "i am comfortable",
]
_BANNED_RE = re.compile("|".join(re.escape(phrase) for phrase in _BANNED), re.I)


def banned_phrases(text: str) -> list[Finding]:
    return [Finding("BANNED_PHRASE", match.group(0)) for match in _BANNED_RE.finditer(text)]


# "across WhatsApp, Telegram, Discord, and Instagram" — the exact shape the prompt tells it to
# abstract away, produced anyway.
_LIST_OF_NAMES = re.compile(
    r"\b[A-Z][\w.&+-]*(?:,\s+[A-Z][\w.&+-]*){2,}(?:,?\s+and\s+[A-Z][\w.&+-]*)?")


def exhaustive_lists(text: str) -> list[Finding]:
    """Three or more proper nouns in a row. `Abstract beats exhaustive` in the live prompt."""
    return [Finding("EXHAUSTIVE_LIST", match.group(0))
            for match in _LIST_OF_NAMES.finditer(text)]


def paragraph_density(blocks: list[str], *, max_sentences: int = 3,
                      max_mean_words: int = 22) -> list[Finding]:
    """Paragraphs a skimming reader gives up on."""
    out = []
    for index, block in enumerate(blocks, start=1):
        parts = sentences(block)
        if len(parts) > max_sentences:
            out.append(Finding("DENSE_PARAGRAPH",
                               f"paragraph {index} has {len(parts)} sentences"))
        if parts:
            mean = len(words(block)) / len(parts)
            if mean > max_mean_words:
                out.append(Finding("LONG_SENTENCES",
                                   f"paragraph {index} averages {mean:.0f} words per sentence"))
    return out


def word_count(text: str, *, low: int = 200, high: int = 260) -> list[Finding]:
    """The band the live prompt asks for, not the 280 its final scan tolerates."""
    count = len(words(text))
    if count < low:
        return [Finding("TOO_SHORT", f"{count} words, under {low}")]
    if count > high:
        return [Finding("TOO_LONG", f"{count} words, over {high}")]
    return []


def cv_lifts(text: str, cv_text: str, *, length: int = 6) -> list[Finding]:
    """Runs of words copied straight out of the CV.

    `Do not reuse stock phrasing from the CV` is unenforceable by eye and trivial to count: any
    word sequence of `length` or more that appears in both documents was lifted. Six is long
    enough that a shared technical term ("event-driven service boundaries") does not trip it and
    short enough to catch a rewritten bullet.
    """
    source = set(_ngrams(cv_text, length))
    if not source:
        return []
    hits = {gram for gram in _ngrams(text, length) if gram in source}
    return [Finding("CV_LIFT", gram) for gram in sorted(hits)]


_FIGURE = re.compile(r"(?i)\b\d[\d,.]*\s*(?:k|m|bn|%|\+)?")


def unsupported_numbers(text: str, cv_text: str) -> list[Finding]:
    """Figures in the letter that are not in the candidate's own facts.

    This is the failure that cannot ship. Asked for a concrete moment, the model supplies concrete
    numbers, and the ones it supplies are not always the ones the candidate has: measured on the
    `b-anecdote` arm, "10k+ records in under 1ms" (a latency) came back as "ingests 10,000+ records
    per second" (a throughput), and a letter acquired "thousands of customers" that appears nowhere
    in the profile. Both read beautifully and both would collapse in an interview.

    Comparison is on the normalised value, so `10k`, `10,000` and `10000` are the same figure and
    only a genuinely new one is reported.
    """
    known = {_figure(match.group(0)) for match in _FIGURE.finditer(cv_text)}
    seen: set[str] = set()
    out = []
    for match in _FIGURE.finditer(text):
        value = _figure(match.group(0))
        if value in known or value in seen:
            continue
        seen.add(value)
        out.append(Finding("NUMBER_NOT_IN_CV", _around(text, match.start())))
    return out


def _figure(raw: str) -> str:
    """`10k`, `10,000` and `10000` are one value; `1ms` and `1` are not the same as `10`."""
    cleaned = raw.lower().replace(",", "").replace(" ", "").rstrip("+")
    # One decimal point at most: a version like `24.04.` and a sentence-ending `1ms.` both arrive
    # here with punctuation attached, and `float("24.04.")` raises.
    digits = re.match(r"\d+(?:\.\d+)?", cleaned)
    if not digits:
        return cleaned
    number = float(digits.group(0))
    if cleaned.endswith("k"):
        number *= 1_000
    elif cleaned.endswith("m"):
        number *= 1_000_000
    elif cleaned.endswith("bn"):
        number *= 1_000_000_000
    return f"{number:.10g}" + ("%" if cleaned.endswith("%") else "")


def _ngrams(text: str, length: int) -> list[str]:
    tokens = [word.lower() for word in words(text)]
    return [" ".join(tokens[i:i + length]) for i in range(len(tokens) - length + 1)]


def _around(text: str, position: int, width: int = 34) -> str:
    start, end = max(0, position - width), min(len(text), position + width)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


# ── the whole letter ──────────────────────────────────────────────────────────────────────────

# Which rules were phrased as a pass over the text in the live prompt, and which asked the model
# to weigh the draft. The split is the measurement: it is how a prompt change is argued.
MECHANICAL = {"COLON", "EMDASH", "CONTRACTION", "NO_NUMBER", "OPEN_NO_COMPANY", "OPEN_NO_TITLE",
              "TOO_SHORT", "TOO_LONG"}
JUDGEMENT = {"OPEN_RESTATES_POSTING", "OPEN_DESCRIBES_COMPANY", "TELLS_THE_MATCH", "WEAK_CLOSE",
             "BANNED_PHRASE", "EXHAUSTIVE_LIST", "DENSE_PARAGRAPH", "LONG_SENTENCES", "CV_LIFT"}

# Neither of the above. A figure the candidate cannot support is not a style preference and not a
# rule the prompt happens to state — it is a claim they would have to retract in an interview, so
# it is counted on its own and a variant that trades it for readability has not won anything.
HONESTY = {"NUMBER_NOT_IN_CV"}


@dataclass(frozen=True)
class Report:
    """Everything one letter did wrong, and how much of it."""

    findings: list[Finding]
    body_words: int
    paragraphs: int

    @property
    def mechanical(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.code in MECHANICAL]

    @property
    def judgement(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.code in JUDGEMENT]

    @property
    def honesty(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.code in HONESTY]

    @property
    def clean(self) -> bool:
        return not self.findings

    def counts(self) -> dict[str, int]:
        """`{'TELLS_THE_MATCH': 3, 'CV_LIFT': 2}` — one line in a log, per rule 8."""
        tally: dict[str, int] = {}
        for finding in self.findings:
            tally[finding.code] = tally.get(finding.code, 0) + 1
        return dict(sorted(tally.items()))


def check(letter: str, *, company: str = "", title: str = "",
          cv_text: str = "", facts_text: str = "") -> Report:
    """Every check, over one finished cover letter.

    `cv_text` and `facts_text` are deliberately separate. Phrasing is judged against the CV,
    because that is the document whose wording must not be reused. Figures are judged against
    everything the candidate may state as true, which is the facts block — the model is grounded
    on that, not on the CV. Checking numbers against the CV alone reports a real metric as an
    invention: measured here, "500+ concurrent updates per second" and "189 unit tests" are both
    the candidate's own and both absent from the base CV.
    """
    blocks = paragraphs(letter)
    prose = " ".join(blocks)

    findings = [
        *colons(prose),
        *emdashes(prose),
        *contractions(prose),
        *no_number(prose),
        *identifies_the_job(prose, company=company, title=title),
        *restating_opening(prose),
        *telling_the_match(prose),
        *weak_close(prose),
        *banned_phrases(prose),
        *exhaustive_lists(prose),
        *paragraph_density(blocks),
        *word_count(prose),
        *(cv_lifts(prose, cv_text) if cv_text else []),
        *(unsupported_numbers(prose, facts_text or cv_text) if (facts_text or cv_text) else []),
    ]
    return Report(findings=findings, body_words=len(words(prose)), paragraphs=len(blocks))
