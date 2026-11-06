"""Which of a list of words a text has, as green and red words are compared. The one rule read by the match
(`libs.matching`), the words index (`libs.corpus.terms`) and the stored text, whose words are found as it is
stored (`CorpusRepository.set_description`). Its own module, needing nothing, so all three can import it."""
from __future__ import annotations

import re
from collections.abc import Iterable
from functools import cache


def norm(text: str) -> str:
    """A word as green and red words are compared: lower case, spaces and hyphens alike."""
    return re.sub(r"[\s-]+", " ", text.strip().lower())


@cache
def _bounded(word: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)")


def words_in(text: str | None, words: Iterable[str]) -> set[str]:
    """The words a text has, as `libs.matching.pattern` finds them: whole words, any case, a space and
    a hyphen alike. Each word is looked for on its own, so "engineer" is found inside "backend
    engineer" too, which one regex of every word would have passed over."""
    hay = norm(text or "")
    return {word for word in words if word in hay and _bounded(word).search(hay)}


def terms_of(title: str | None, description: str | None, words: Iterable[str]) -> dict[str, list[str]]:
    """A posting's `terms`, the words it has anywhere, and `title_terms`, those in its title."""
    words = list(words)
    in_title = words_in(title, words)
    return {"terms": sorted(in_title | words_in(description, words)), "title_terms": sorted(in_title)}
