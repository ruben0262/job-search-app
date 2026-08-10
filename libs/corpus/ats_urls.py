"""Reading a company's ATS board out of a posting URL we already stored.

**The company list was a closed loop.** Tokens came from one place — hiring.cafe's `board_token`
field, via `harvest_from_hits` — and hiring.cafe is queried with the users' keywords. So the
companies discovered were the companies our existing users already searched for, which made the
ATS tier as narrow as the corpus that seeded it, which made the corpus narrower still. Sampled on
2026-08-09, all 336 live tokens were software startups: a mechanical engineer had 2.9% of the
platform to look at, and nine of seventeen enabled sources are ATS boards polling that list.

Every other source already carries the same signal and it was being thrown away. A LinkedIn or
Adzuna posting for an automotive company links to `boards.greenhouse.io/<company>/jobs/…`, and the
token is right there in the URL. Measured against the live corpus on 2026-08-10: 592 companies
with an ATS-shaped URL, **171 of them boards we were not polling** — a 52% increase on the token
list, from rows already fetched, described and stored.

That is also what makes it self-widening rather than a one-off import. A new user from a new field
brings their own keywords; those keywords surface their industry's companies on the aggregators;
those postings carry ATS URLs; and the boards join the list. Nobody curates it.

The patterns are deliberately narrow. A wrong token is a board that 404s every cycle until
`mark_dead` catches it, so anything that does not clearly name a company is skipped rather than
guessed at.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# One pattern per ATS, each anchored on that vendor's own host so a lookalike path cannot match.
# The captured group is the board token as the vendor's API expects it.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # boards.greenhouse.io/acme/jobs/123, job-boards.greenhouse.io/acme/jobs/123
    ("greenhouse", re.compile(r"https?://(?:[\w-]+\.)?greenhouse\.io/(?:embed/job_board\?for=)?"
                              r"([A-Za-z0-9_-]+)", re.I)),
    # jobs.lever.co/acme/uuid
    ("lever", re.compile(r"https?://jobs\.lever\.co/([A-Za-z0-9_-]+)", re.I)),
    # jobs.ashbyhq.com/acme/uuid — tokens here may carry a dot (`cytora.com`).
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)", re.I)),
    # careers.smartrecruiters.com/Acme/123, jobs.smartrecruiters.com/Acme/123
    ("smartrecruiters", re.compile(r"https?://(?:careers|jobs)\.smartrecruiters\.com/"
                                   r"([A-Za-z0-9_-]+)", re.I)),
    # acme.recruitee.com/o/role
    ("recruitee", re.compile(r"https?://([A-Za-z0-9_-]+)\.recruitee\.com", re.I)),
    # acme.breezy.hr/p/uuid
    ("breezy", re.compile(r"https?://([A-Za-z0-9_-]+)\.breezy\.hr", re.I)),
    # apply.workable.com/acme/j/ABC — the bare `acme.workable.com` host is legacy but still seen.
    ("workable", re.compile(r"https?://apply\.workable\.com/([A-Za-z0-9_-]+)"
                            r"|https?://([A-Za-z0-9_-]+)\.workable\.com", re.I)),
    # acme.wd3.myworkdayjobs.com/en-US/board/job/…
    ("workday", re.compile(r"https?://([A-Za-z0-9_-]+)\.[\w-]*\.?myworkdayjobs\.com", re.I)),
    # acme.jobs.personio.de / .com
    ("personio", re.compile(r"https?://([A-Za-z0-9_-]+)\.jobs\.personio\.[a-z]{2,3}", re.I)),
)

# Path segments that are part of the vendor's own site rather than a company's board. Matching one
# would enrol a board that cannot exist and 404s every cycle until it is marked dead.
_NOT_A_COMPANY = frozenset({
    "www", "api", "app", "jobs", "job", "careers", "career", "embed", "boards", "board",
    "search", "static", "assets", "cdn", "help", "support", "about", "login", "signup",
    "privacy", "terms", "blog", "en", "en-us", "companies", "company", "s", "j", "o", "p",
})


def token_from_url(url: str | None) -> tuple[str, str] | None:
    """`(ats, token)` for a posting URL, or `None` when it names no company board."""
    if not url:
        return None
    for ats, pattern in _PATTERNS:
        found = pattern.search(url)
        if not found:
            continue
        # `workable` has two alternatives, so take whichever group matched.
        token = next((group for group in found.groups() if group), "")
        token = token.strip().strip(".-").lower()
        if not token or token in _NOT_A_COMPANY or len(token) < 2:
            continue
        return ats, token
    return None


def tokens_from_urls(urls: Iterable[str | None]) -> list[tuple[str, str]]:
    """Every distinct `(ats, token)` these URLs name, order-preserving."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for url in urls:
        pair = token_from_url(url)
        if pair is not None and pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out
