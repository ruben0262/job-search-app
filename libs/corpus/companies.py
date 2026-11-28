"""What a company says of itself, for the writer of a cover letter (owner, 2026-11-27: "lets build the
company knowledge ... saving it in db so we can access it anytime without further prompt tokens being spent").

The first source is the one the platform already holds: the company's own postings. Most say what the company
is under a heading ("About us", "Who we are"), and a company with several postings says it in each, so a
posting that says nothing of its employer (a short one, or one put up by somebody else) still has it. It is
gathered by rule, once a company, and kept in `companies` for good (owner, 2026-11-28: "keep company data
always, even if its 30 days or older").

Where its postings say nothing, the writer of the first letter for it looks up the company's own website
(owner, 2026-11-28: "if another user then requests it, its an easy lookup in our db"), and what it read is
kept here by `learned`, once checked: from a page on a site named for the company, as plain text. Only 2% of
postings link to a site of their own (measured 2026-11-27), so the writer finds it by searching.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import datetime
from html import unescape
from urllib.parse import urlparse

from pymongo.database import Database

from libs.corpus.texts import texts
from libs.db import now
from libs.db.models import Company, CorpusJob

# As much as a letter's writer is given: a paragraph, not a brochure.
ABOUT_MAX = 900
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
    """What this company says of itself: from its row where it has one, for good, else gathered from its
    newest postings and kept: the part most of them have under a heading that names the company, else the
    paragraph they share. Empty for a company whose postings say nothing of it and whose website was not
    read."""
    at = at or now()
    if not (company := key(name)):
        return ""
    rows = db[Company.COLLECTION]
    if kept := rows.find_one({"_id": company}):
        return kept["about"]
    ids = [row["_id"] for row in db[CorpusJob.COLLECTION].find({"company": name}, {"_id": 1})
           .sort("first_seen_at", -1).limit(POSTINGS)]
    postings = list(texts(db, ids).values())
    under = Counter(part for posting in postings if (part := about_in(posting, name)))
    said = under.most_common(1)[0][0] if under else _repeated(postings)
    rows.replace_one({"_id": company}, Company(id=company, name=(name or "").strip(), about=said,
                                               postings=len(postings), read_at=at).to_doc(), upsert=True)
    return said


def to_look_up(db: Database, name: str | None) -> bool:
    """Whether this company's website is still to be looked for: its row says nothing of it and nobody has
    looked. Asked once a company, whatever was found."""
    kept = db[Company.COLLECTION].find_one({"_id": key(name)}) if key(name) else None
    return bool(kept) and not kept["about"] and not kept.get("searched")


# Words a company's name shares with any other's: never what tells its website apart.
_GENERIC = frozenset(("ltd", "limited", "plc", "inc", "llc", "gmbh", "ag", "sa", "sas", "srl", "bv", "nv",
                      "spa", "kft", "zrt", "sp", "zoo", "co", "company", "companies", "group", "holdings",
                      "international", "services", "solutions", "the", "and", "of", "for", "uk", "eu",
                      "europe", "global", "kg", "ohg", "kgaa", "ug", "se", "ev"))
# Where a company's page is not its own: boards, networks and encyclopaedias.
_NOT_ITS_OWN = re.compile(r"(?i)(?:^|\.)(?:linkedin|indeed|glassdoor|totaljobs|reed|monster|ziprecruiter|"
                          r"jooble|adzuna|cv-library|stepstone|pracuj|xing|wikipedia|facebook|instagram|x|"
                          r"twitter|youtube|crunchbase|bloomberg|companieshouse|find-and-update)\.")


_FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|my|me)\b")
# What a company says of itself never holds a key or a sign-in token: a run of 32 or more characters with no
# space, or a JSON web token. Refused whole, should a page ever talk a model into giving one back.
_SECRET = re.compile(r"[A-Za-z0-9_\-+/=.]{32,}|\beyJ[A-Za-z0-9_\-]{10,}")


def own_site(name: str | None, source: str) -> bool:
    """Whether `source` is a page on the company's own website: an http(s) address on a host that carries a
    word of its name of three letters or more ("hager.co.uk" for Hager UK, "1dea.pl" for 1dea), and not a job
    board, a network or an encyclopaedia. A rule, so that a page about another company of a like name, or
    about this one on somebody else's site, is never kept as what it says of itself."""
    address = urlparse((source or "").strip())
    host = (address.hostname or "").casefold()
    if address.scheme not in ("http", "https") or not host or _NOT_ITS_OWN.search(host):
        return False
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().casefold()
    words = [word for word in re.findall(r"[a-z0-9]+", plain) if word not in _GENERIC]
    squeezed = re.sub(r"[^a-z0-9]", "", host)
    # Or by its initials, three or more, as the host's first part: "bcuhb.nhs.wales" for Betsi Cadwaladr
    # University Health Board, "jrs.de" for J. Rettenmaier & Söhne.
    initials = "".join(word[0] for word in words)
    return any(word in squeezed for word in words if len(word) >= 3) or (
        len(initials) >= 3 and host.removeprefix("www.").split(".")[0] == initials)


def learned(db: Database, name: str | None, note: str, source: str, *, at: datetime | None = None) -> str:
    """Keep what a letter's writer read about this company on its own website, as plain text cut to
    `ABOUT_MAX`, and give it back; empty, and nothing kept, where it is not from `own_site` or says nothing.
    Either way the company is marked as looked up, so that no other letter asks again."""
    if not (company := key(name)):
        return ""
    plain = " ".join(unescape(re.sub(r"<[^>]*>", " ", note or "")).split())
    # A remark of the model's own ("Note: I could only see this page through search-result excerpts") is not
    # the company's: a company speaks of itself as "we", and a note of it in the third person.
    sentences = re.split(r"(?<=[.!?])\s+", plain)
    said = _cut(" ".join(part for part in sentences if not _FIRST_PERSON.search(part)))
    kept = said if len(said.split()) >= 12 and own_site(name, source) and not _SECRET.search(said) else ""
    db[Company.COLLECTION].update_one(
        {"_id": company},
        {"$set": {"searched": True, **({"about": kept, "source": source.strip(), "read_at": at or now()}
                                       if kept else {})},
         "$setOnInsert": {"name": (name or "").strip(), "postings": 0}},
        upsert=True)
    return kept
