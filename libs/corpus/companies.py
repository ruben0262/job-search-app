"""What a company says of itself, for the writer of a cover letter (owner, 2026-11-27: "lets build the
company knowledge ... saving it in db so we can access it anytime without further prompt tokens being spent").

The source is the one the platform already holds and can trust: the company's own postings. Most say what
the company is under a heading ("About us", "Who we are"), and a company with several postings says it in
each, so a posting that says nothing of its employer (a short one, or one put up by somebody else) still has
it. It is gathered by rule, once a company, kept in `companies`, and gathered again when it is `GATHERED_FOR`
old, by which time the company has put up other postings. Reading a company's own site would say more, and
only 2% of postings link to one (measured 2026-11-27): that source waits for a way to find a site by name.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from pymongo.database import Database

from libs.corpus.texts import texts
from libs.db import now
from libs.db.models import Company, CorpusJob

# As much as a letter's writer is given: a paragraph, not a brochure.
ABOUT_MAX = 900
# How long what was gathered is used before the company's postings are read again.
GATHERED_FOR = timedelta(days=30)
# How many of a company's newest postings are read for it.
POSTINGS = 8

_NAMED = (r"about (?:us|the company|the organi[sz]ation|the business|our company)|who we are|"
          r"our (?:company|story|mission)|company (?:overview|description|profile)|the company|über uns|"
          r"wer wir sind|o nas|o firmie|rólunk|a cégről|over ons|chi siamo|à propos(?: de nous)?|"
          r"qui sommes-nous|sobre nosotros|quiénes somos")
# A line that heads another part of a posting: where the company's own part ends.
_NEXT = re.compile(r"(?i)^\W*(?:the role|about the (?:role|job|position|team)|your (?:role|profile|tasks)|"
                   r"what you|what we (?:offer|are looking)|responsibilities|requirements|qualifications|"
                   r"benefits|we offer|key (?:duties|responsibilities)|job description|duties|skills|"
                   r"who you are|the opportunity|how to apply|location|salary|hours)\b")


def key(name: str | None) -> str:
    """A company's name as its row is kept: case and spacing aside."""
    return " ".join((name or "").casefold().split())


def about_in(posting: str | None, name: str | None = None) -> str:
    """What one posting says of its company: the lines under the heading that names it ("About us", "Who we
    are", "About Acme"), up to the next heading, cut at a sentence's end within `ABOUT_MAX`. Empty when the
    posting has no such part."""
    named = _NAMED + (rf"|about {re.escape(name.strip())}" if (name or "").strip() else "")
    heading = re.compile(rf"(?i)^\W*(?:{named})\W*$")
    said: list[str] = []
    inside = False
    for raw in (posting or "").splitlines():
        line = " ".join(raw.split())
        if not inside:
            inside = bool(heading.match(line))
            continue
        if not line:
            continue
        if _NEXT.match(line) or heading.match(line) or (len(line.split()) <= 5 and line.endswith(":")):
            break
        said.append(line)
        if sum(map(len, said)) >= ABOUT_MAX:
            break
    return _cut(" ".join(said))


def _cut(text: str) -> str:
    if len(text) <= ABOUT_MAX:
        return text
    kept = text[:ABOUT_MAX]
    end = max(kept.rfind(". "), kept.rfind("! "), kept.rfind("? "))
    return kept[:end + 1] if end > ABOUT_MAX // 3 else kept.rsplit(" ", 1)[0]


def _repeated(postings: list[str]) -> str:
    """The paragraph a company puts in more than one of its postings and that speaks as the company ("we",
    "our"): its own description, where no heading names it."""
    seen: Counter[str] = Counter()
    for posting in postings:
        seen.update({" ".join(part.split()) for part in re.split(r"\n\s*\n|\n", posting)
                     if len(part.split()) >= 15})
    ours = [(count, len(part), part) for part, count in seen.items()
            if count >= 2 and re.search(r"(?i)\b(?:we|our|us)\b", part)]
    return _cut(max(ours)[2]) if ours else ""


def about(db: Database, name: str | None, *, at: datetime | None = None) -> str:
    """What this company says of itself, from its row where that is fresh, else gathered from its newest
    postings and kept: the part most of them have under a heading that names the company, else the paragraph
    they share. Empty for a company whose postings say nothing of it."""
    at = at or now()
    if not (company := key(name)):
        return ""
    rows = db[Company.COLLECTION]
    kept = rows.find_one({"_id": company})
    read_at = kept and kept["read_at"].replace(tzinfo=at.tzinfo)
    if kept and at - read_at < GATHERED_FOR:
        return kept["about"]
    ids = [row["_id"] for row in db[CorpusJob.COLLECTION].find({"company": name}, {"_id": 1})
           .sort("first_seen_at", -1).limit(POSTINGS)]
    postings = list(texts(db, ids).values())
    under = Counter(part for posting in postings if (part := about_in(posting, name)))
    said = under.most_common(1)[0][0] if under else _repeated(postings)
    rows.replace_one({"_id": company}, Company(id=company, name=(name or "").strip(), about=said,
                                               postings=len(postings), read_at=at).to_doc(), upsert=True)
    return said
