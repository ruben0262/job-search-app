"""What a fit score has to beat: the order the Jobs page uses today, and chance."""
from __future__ import annotations

import random

from libs.corpus.words import words_in
from libs.matching import green_words


def words_found(person: str, documents: list, search: dict, posting: dict) -> float:
    """The Jobs page's "best" order (`libs.matching.ORDERS`): the most of their words, then those in the
    title."""
    words = green_words({"sections": [{"keywords": search["words"]}]})
    found = words_in(posting["title"], words) | words_in(posting.get("description"), words)
    return len(found) + 0.5 * len(words_in(posting["title"], words))


def chance(person: str, documents: list, search: dict, posting: dict) -> float:
    return random.Random(f"{person}-{posting['id']}").random()  # noqa: S311 - a seeded draw, not a secret
