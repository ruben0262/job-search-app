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
    # Added 2026-08-05 from names that appeared unresolved in the live corpus.
    "philippines": "PH", "china": "CN", "colombia": "CO", "south korea": "KR", "korea": "KR",
    "south africa": "ZA", "argentina": "AR", "chile": "CL", "vietnam": "VN", "viet nam": "VN",
    "malaysia": "MY", "indonesia": "ID", "thailand": "TH", "pakistan": "PK", "egypt": "EG",
    "nigeria": "NG", "kenya": "KE", "israel": "IL", "new zealand": "NZ", "russia": "RU",
    "united arab emirates": "AE", "saudi arabia": "SA", "moldova": "MD", "georgia": "GE",
    "armenia": "AM", "belarus": "BY", "bosnia": "BA", "albania": "AL", "north macedonia": "MK",
    "montenegro": "ME", "kosovo": "XK",
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
    # Added 2026-08-05 from the location strings actually in the corpus, ranked by how many rows
    # each one accounted for. **A city resolves a "City, XX" string before the code beside it is
    # considered**, which is what makes the trailing-code rule below safe: every genuine
    # US-state/ISO collision in the live data — `Stuttgart, DE`, `Bengaluru, IN`, `Atlanta, GA` —
    # is settled by the city and never reaches the ambiguous case (ROADMAP 6aj).
    "stuttgart": "DE", "leipzig": "DE", "düsseldorf": "DE", "dusseldorf": "DE", "dresden": "DE",
    "nuremberg": "DE", "nürnberg": "DE", "essen": "DE", "bremen": "DE", "hannover": "DE",
    "karlsruhe": "DE", "mannheim": "DE",
    "warszawa": "PL", "katowice": "PL", "łódź": "PL", "lodz": "PL", "szczecin": "PL",
    "gdynia": "PL", "lublin": "PL", "bydgoszcz": "PL", "rzeszów": "PL", "rzeszow": "PL",
    "cardiff": "GB", "glasgow": "GB", "leeds": "GB", "birmingham": "GB", "liverpool": "GB",
    "sheffield": "GB", "belfast": "GB", "reading": "GB", "oxford": "GB",
    "toulouse": "FR", "lyon": "FR", "marseille": "FR", "bordeaux": "FR", "lille": "FR",
    "nantes": "FR", "courbevoie": "FR", "aix-en-provence": "FR",
    "lisboa": "PT", "porto": "PT", "madrid": "ES", "sevilla": "ES", "malaga": "ES",
    "zagreb": "HR", "ljubljana": "SI", "tallinn": "EE", "vilnius": "LT", "riga": "LV",
    "kyiv": "UA", "kiev": "UA", "belgrade": "RS", "istanbul": "TR",
    "amsterdam": "NL", "the hague": "NL", "noordwijk": "NL", "antwerp": "BE", "ghent": "BE",
    "zurich": "CH", "geneva": "CH", "basel": "CH", "bern": "CH",
    "gothenburg": "SE", "malmö": "SE", "malmo": "SE", "aarhus": "DK", "tampere": "FI",
    # US cities, which is what LinkedIn's "City, ST" strings are, and the largest single block of
    # unresolved rows before this.
    "washington": "US", "los angeles": "US", "san diego": "US", "san jose": "US",
    "santa clara": "US", "mountain view": "US", "palo alto": "US", "sunnyvale": "US",
    "atlanta": "US", "dallas": "US", "houston": "US", "denver": "US", "phoenix": "US",
    "philadelphia": "US", "miami": "US", "charlotte": "US", "detroit": "US", "dearborn": "US",
    "minneapolis": "US", "portland": "US", "las vegas": "US", "salt lake city": "US",
    "nashville": "US", "columbus": "US", "cleveland": "US", "pittsburgh": "US", "raleigh": "US",
    "mclean": "US", "arlington": "US", "baltimore": "US", "st. louis": "US", "kansas city": "US",
    "new orleans": "US", "san antonio": "US", "sacramento": "US", "orlando": "US", "tampa": "US",
    "jersey city": "US", "stamford": "US", "huntsville": "US", "omaha": "US", "bentonville": "US",
    "overland park": "US", "cedar rapids": "US", "honolulu": "US", "boulder": "US", "irvine": "US",
    # Non-European cities that recur and would otherwise read as unknown.
    "bengaluru": "IN", "bangalore": "IN", "hyderabad": "IN", "pune": "IN", "mumbai": "IN",
    "chennai": "IN", "gurgaon": "IN", "noida": "IN",
    "são paulo": "BR", "sao paulo": "BR", "rio de janeiro": "BR",
    "shanghai": "CN", "beijing": "CN", "shenzhen": "CN", "seoul": "KR",
    "tokyo": "JP", "yokohama": "JP", "osaka": "JP",
    "ho chi minh": "VN", "hanoi": "VN", "bayan lepas": "MY", "kuala lumpur": "MY",
    "manila": "PH", "jakarta": "ID", "bangkok": "TH", "dubai": "AE", "tel aviv": "IL",
    "sydney": "AU", "melbourne": "AU", "auckland": "NZ", "montreal": "CA", "ottawa": "CA",
    # The accented forms boards actually write, beside the plain ones already above — "gdansk" and
    # "gdańsk" are two strings and only one of them was matching.
    "gdańsk": "PL", "poznań": "PL", "gliwice": "PL",
    "heidelberg": "DE", "darmstadt": "DE", "mainz": "DE", "bochum": "DE", "münster": "DE",
    "muenster": "DE", "würzburg": "DE", "wuerzburg": "DE", "köln": "DE", "koeln": "DE",
    "augsburg": "DE", "wiesbaden": "DE", "bonn": "DE", "kiel": "DE", "regensburg": "DE",
    "brighton": "GB", "nottingham": "GB", "newcastle": "GB", "southampton": "GB", "leicester": "GB",
    "fremont": "US", "menlo park": "US", "indianapolis": "US", "redmond": "US", "bellevue": "US",
    "cupertino": "US", "santa monica": "US", "brooklyn": "US", "cincinnati": "US", "milwaukee": "US",
}

# US state codes, for the `City, ST` form that is LinkedIn's house style for American postings.
_US_STATES: frozenset[str] = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})

# Every ISO alpha-2 code the name table above can produce. Built from it rather than typed out, so
# a country added there is understood in code form for free and the two cannot drift.
_ISO_CODES: frozenset[str] = frozenset(_COUNTRY_NAMES.values()) | frozenset(_CITY_COUNTRY.values())

# **The codes that mean two things, which is why the trailing-code rule refuses them.**
#
# `Stuttgart, DE` is Germany and `Wilmington, DE` is Delaware; `Bengaluru, IN` is India and
# `Indianapolis, IN` is Indiana. Twelve such collisions occur in the live corpus. Every one of them
# is settled by the city name, which is matched first — so this set exists for the case where the
# city is *unknown*, and there the honest answer is "cannot tell". Returning a guess would hide a
# European job behind a US label, and rule 2 of this module is that a wrong country is the one
# error that costs somebody a role.
_AMBIGUOUS_CODES: frozenset[str] = _US_STATES & _ISO_CODES

# A place, then a two-letter code, at the end of the string: "Toulouse, FR", "Atlanta, GA".
_TRAILING_CODE = re.compile(r",\s*([A-Za-z]{2})\s*$")

_PLACES: dict[str, str] = {**_EXTRA_CITIES, **_CITY_COUNTRY, **_COUNTRY_NAMES}

# **One alternation, scanned once**, rather than one regex per place scanned in turn.
#
# The table is ~400 names, and a name-at-a-time scan is 400 searches for every posting — measured at
# 64 microseconds each, against 27 before the table grew. This is one pass: 5 microseconds, and it
# stops the cost of adding a place name from being paid on every row forever (ROADMAP 6aj).
#
# Word-boundary matching either way, or "us" fires inside "customer" and "Austria" resolves to the
# US. That is the difference between a filter and a random number generator.
_PLACE_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(name) for name in sorted(_PLACES, key=len, reverse=True))
    + r")(?![a-z0-9])")


def _place_in(haystack: str) -> str | None:
    """The country of the **longest** place name in the string, or `None`.

    Longest rather than first, which is the rule the name-at-a-time scan encoded by sorting the
    table: "US Remote - Berlin" is a Berlin job, and taking the earliest match would read it as
    American. One `finditer` gives every candidate in a single pass, so keeping the rule costs
    nothing.
    """
    best: str | None = None
    longest = 0
    for match in _PLACE_RE.finditer(haystack):
        name = match.group(1)
        if len(name) > longest:
            best, longest = _PLACES[name], len(name)
    return best


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
        found = _place_in(haystack)
        if found is not None:
            return found

    # **Only after every name has failed.** A board that writes "City, XX" nearly always writes a
    # city we know, and the city is the better answer — this is the tail: places absent from the
    # table above, where the code beside them is the only thing left to read.
    return _trailing_code(location)


def _trailing_code(location: str | None) -> str | None:
    """The country from a trailing two-letter code, or `None` when it cannot be read as one.

    Two formats, one pattern. SmartRecruiters writes ISO country codes (`Toulouse, FR`) and
    LinkedIn writes US state codes (`Atlanta, GA`), and both are "place, two letters" — so the code
    decides which, and a code that could be either is refused rather than guessed.
    """
    match = _TRAILING_CODE.search(location or "")
    if match is None:
        return None
    code = match.group(1).upper()
    if code in _AMBIGUOUS_CODES:
        return None
    if code in _US_STATES:
        return "US"
    return code if code in _ISO_CODES else None


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
