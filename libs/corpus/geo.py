"""What country a posting is in, and which countries a user has actually asked for.

**The one filter that is a fact rather than a phrasing.** Every other cheap pre-filter this project
has considered matched on wording, and wording is where it went wrong: 239 of 376 jobs scoring ≥60
had a keyword in the title, so a title match discards a third of the good ones — the unusually
worded ones, which are often the interesting ones (ROADMAP 6o.1). A country is not a matter of how
somebody wrote the advert. "Berlin, Germany" is in Germany whatever the job is called.

## The three rules

1. **Hard.** A user who did not ask for the UK does not get UK jobs. Nothing in the platform
   enforces this today: `libs.scoring.rules._location_points` awards *full marks* to any posting
   once the user has picked a region, on the grounds that "no posting spells `european_union` in its
   location field". So selecting the EU currently excludes nothing, and a London job scores exactly
   like a Berlin one.
2. **Unknown passes.** A posting that names no country — which remote roles frequently do on
   purpose — is kept. Rejecting those would throw away the most portable jobs on the board, and
   absence of evidence is not evidence of absence, which is the same rule `_location_points` and
   `workfilter` already follow.
3. **The EU is a list, and the UK is not on it.** That is the whole trick, and it is why this module
   exists rather than a `LIKE` against the location text.

## Deliberately not a geocoder

`_CITIES` names the cities the config vocabulary offers and nothing else. A general place-name
database would be a dependency, a data-refresh problem, and a source of confident wrong answers for
the long tail of ambiguous names — and the escape hatch above means an unrecognised place costs a
posting nothing. Wrong here is expensive; unknown here is free.
"""
from __future__ import annotations

import re

# ISO 3166-1 alpha-2, because a two-letter code is stable, indexable, and unambiguous in a way that
# "Germany" / "Deutschland" / "DE" is not.

# The 27 member states, 2026. **The UK is not among them and that is the point of this constant.**
# Brexit is the case the owner raised: a user searching Europe who receives London jobs is being
# shown work they cannot take, and no ranking signal fixes a hard eligibility fact.
#
# **One region slug, and it carries this set** (decided 2026-08-04). There used to be two —
# `european_union` and `europe` — which is a distinction nobody reading a checkbox would draw
# correctly, and getting it wrong silently changes which jobs somebody sees. So there is one, it is
# called `europe`, and it means the union. Anyone wanting the UK, Switzerland or Norway names them.
EU_COUNTRIES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})

# Every city the config vocabulary offers, and the country it is in. Naming a city cannot exclude
# the country around it: somebody who asked for Budapest has plainly not ruled out Hungary.
_CITY_COUNTRY: dict[str, str] = {
    "london": "GB", "berlin": "DE", "amsterdam": "NL", "dublin": "IE", "paris": "FR",
    "madrid": "ES", "lisbon": "PT", "warsaw": "PL", "budapest": "HU", "zurich": "CH",
    "stockholm": "SE", "copenhagen": "DK",
}

# Country-level slugs in the config vocabulary.
_COUNTRY_SLUG: dict[str, str] = {
    "united_kingdom": "GB", "ireland": "IE", "switzerland": "CH", "norway": "NO",
}

# The one region slug.
_REGION_SLUGS: dict[str, frozenset[str]] = {"europe": EU_COUNTRIES}

# **No `remote_*` slugs here, deliberately.** Remoteness is not a place. It used to be four entries
# in this vocabulary — `remote_eu`, `remote_uk`, `remote_us`, `remote_worldwide` — which made
# "where" and "how" one question with answers that could contradict each other, and duplicated a
# `remote_policy` field the config already had. Working arrangement is its own filter now
# (`libs.corpus.workfilter`), and this module answers only "which country".


def wanted_countries(locations: list[str] | tuple[str, ...] | None) -> frozenset[str] | None:
    """The countries a user's location selection admits. `None` means "do not filter".

    `None` rather than "every country" for the unrestricted case, because the two behave differently
    against a posting whose country we could not read: a set would have to special-case it, and a
    caller that forgets is a caller that quietly drops every unknown posting.

    An empty selection also returns `None`. A user who named no location has not excluded anywhere.
    """
    if not locations:
        return None

    allowed: set[str] = set()
    for raw in locations:
        slug = str(raw or "").strip().lower()
        if not slug:
            continue
        if slug in _REGION_SLUGS:
            allowed |= _REGION_SLUGS[slug]
        elif slug in _COUNTRY_SLUG:
            allowed.add(_COUNTRY_SLUG[slug])
        elif slug in _CITY_COUNTRY:
            allowed.add(_CITY_COUNTRY[slug])
        # An unrecognised slug contributes nothing rather than widening the set. A vocabulary entry
        # added here without a country belongs in this module, and failing open on it would let a
        # typo quietly disable somebody's country filter.

    return frozenset(allowed) or None


# ── reading a country off a posting ──────────────────────────────────────────────────────────────
#
# Names as boards actually write them, longest first so "united kingdom" is tried before "united".
# Only the forms seen in real location strings — this is not a gazetteer, and an unrecognised place
# is free (rule 2) while a wrong one is not.
_COUNTRY_NAMES: dict[str, str] = {
    "united kingdom": "GB", "great britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB", "uk": "GB",
    "united states": "US", "usa": "US", "u.s.": "US", "us": "US",
    "germany": "DE", "deutschland": "DE",
    "netherlands": "NL", "holland": "NL",
    "ireland": "IE", "republic of ireland": "IE",
    "france": "FR", "spain": "ES", "portugal": "PT", "italy": "IT", "poland": "PL",
    "hungary": "HU", "magyarország": "HU", "austria": "AT", "belgium": "BE", "bulgaria": "BG",
    "croatia": "HR", "cyprus": "CY", "czechia": "CZ", "czech republic": "CZ", "denmark": "DK",
    "estonia": "EE", "finland": "FI", "greece": "GR", "latvia": "LV", "lithuania": "LT",
    "luxembourg": "LU", "malta": "MT", "romania": "RO", "slovakia": "SK", "slovenia": "SI",
    "sweden": "SE", "switzerland": "CH", "schweiz": "CH", "norway": "NO", "iceland": "IS",
    "ukraine": "UA", "serbia": "RS", "turkey": "TR", "canada": "CA", "australia": "AU",
    "india": "IN", "brazil": "BR", "mexico": "MX", "japan": "JP", "singapore": "SG",
}

# Cities that identify a country on their own, beyond the config vocabulary — these turn up
# constantly in location strings with no country beside them ("Munich", "Barcelona").
_EXTRA_CITIES: dict[str, str] = {
    "munich": "DE", "münchen": "DE", "hamburg": "DE", "frankfurt": "DE", "cologne": "DE",
    "barcelona": "ES", "valencia": "ES", "milan": "IT", "rome": "IT", "krakow": "PL",
    "kraków": "PL", "wroclaw": "PL", "wrocław": "PL", "gdansk": "PL", "poznan": "PL",
    "brussels": "BE", "vienna": "AT", "prague": "CZ", "bratislava": "SK", "sofia": "BG",
    "bucharest": "RO", "athens": "GR", "helsinki": "FI", "oslo": "NO", "rotterdam": "NL",
    "utrecht": "NL", "eindhoven": "NL", "manchester": "GB", "edinburgh": "GB", "bristol": "GB",
    "cambridge": "GB", "new york": "US", "san francisco": "US", "austin": "US", "seattle": "US",
    "boston": "US", "chicago": "US", "toronto": "CA", "vancouver": "CA",
}

_PLACES: dict[str, str] = {**_EXTRA_CITIES, **_CITY_COUNTRY, **_COUNTRY_NAMES}

# Word-boundary matching, or "us" fires inside "customer" and "Austria" resolves to the US. This is
# the difference between a filter and a random number generator.
_MATCHERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])"), code)
    for name, code in sorted(_PLACES.items(), key=lambda kv: -len(kv[0]))
]


def read_country(location: str | None, *, title: str | None = None) -> str | None:
    """The ISO country code a posting is in, or `None` when it does not say.

    Reads the location string first and the title only as a fallback: a title mentioning a country
    ("Backend Engineer, Germany") is a real signal, but a location string is what a board means by
    where the job is, and preferring the title would let "US-style benefits" relocate a Berlin job.

    Returns `None` for anything it cannot place, and that is a pass rather than a rejection — the
    long tail of place names is where a gazetteer would produce confident wrong answers, and a
    wrong country here removes a job somebody wanted.
    """
    for text in (location, title):
        haystack = (text or "").casefold()
        if not haystack:
            continue
        for pattern, code in _MATCHERS:
            if pattern.search(haystack):
                return code
    return None


def allows(country: str | None, wanted: frozenset[str] | None) -> bool:
    """Whether a posting in `country` may be shown to somebody who asked for `wanted`.

    The two escapes are the whole design: no selection means no filter, and no readable country
    means the posting is kept.
    """
    if wanted is None:
        return True
    if country is None:
        return True
    return country in wanted
