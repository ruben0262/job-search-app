"""A board's name as people write it, and where a job from it is applied to: one answer, for the Jobs page
(through the feed) and a Telegram alert alike. Nobody applies through this site (owner, 2026-11-06)."""
from __future__ import annotations

from libs.corpus.repository import LINK, TYPED

# Anything not listed is its own name, capitalised.
BOARD_NAMES = {"linkedin": "LinkedIn", "justjoinit": "Just Join IT", "nofluffjobs": "No Fluff Jobs",
               "workingnomads": "Working Nomads", "themuse": "The Muse",
               "smartrecruiters": "SmartRecruiters", LINK: "Added by link"}


def board_name(source: str) -> str:
    """"LinkedIn", "No Fluff Jobs", "Greenhouse": what the board is called."""
    return BOARD_NAMES.get(source) or source[:1].upper() + source[1:]


def apply_on(source: str) -> str:
    """What follows "Apply on": the board's name, or "the job's site" for a job somebody added, whose board
    is wherever its link goes."""
    return "the job's site" if source in (LINK, TYPED) else board_name(source)
