"""A posting's stated pay as one figure, the top of it in euros a year, and a person's minimum as the same:
what `libs.matching` compares in the database. Read once, when a posting's text is stored
(`CorpusRepository.set_description`), so a match never reads a description for it.

`libs.corpus.salary` is kept byte-identical to the reference instance, so its reader and its one converter are
borrowed here rather than changed there."""
from __future__ import annotations

from libs.corpus import salary


def top_eur(description: str | None) -> float | None:
    """The top of the pay a posting states, in euros a year; None when it states none, or none with a period,
    since "€5,000" could be a month or a year and says nothing either way."""
    stated = salary.extract(description)
    if not stated or not stated["period"]:
        return None
    return salary._to_eur_annual(stated["max"], stated["currency"], stated["period"])


def floor_eur(config: dict | None) -> float | None:
    """A person's minimum, in euros a year; None when they set none."""
    minimum = (config or {}).get("min_salary")
    if not minimum:
        return None
    return salary._to_eur_annual(minimum, (config or {}).get("salary_currency") or "EUR", "year")
