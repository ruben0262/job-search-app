"""What country a posting is in, and which countries a user has actually asked for."""
from __future__ import annotations

import re

# ISO 3166-1 alpha-2: a two-letter code is stable, indexable and unambiguous where a name is not.

# The 27 member states, 2026.
EU_COUNTRIES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})

# Every city the config vocabulary offers, and the country it is in.
_CITY_COUNTRY: dict[str, str] = {
    "london": "GB", "berlin": "DE", "amsterdam": "NL", "dublin": "IE", "paris": "FR",
    "madrid": "ES", "lisbon": "PT", "warsaw": "PL", "budapest": "HU", "zurich": "CH",
    "stockholm": "SE", "copenhagen": "DK",
}

# Country-level slugs in the config vocabulary.
_COUNTRY_SLUG: dict[str, str] = {
    "united_kingdom": "GB", "ireland": "IE", "switzerland": "CH", "norway": "NO",
}

# The one region slug; `remote_*` is a workplace mode rather than a place, so it is absent.
_REGION_SLUGS: dict[str, frozenset[str]] = {"europe": EU_COUNTRIES}


def wanted_countries(locations: list[str] | tuple[str, ...] | None) -> frozenset[str] | None:
    """The countries a user's location selection admits."""
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
        # An unrecognised slug contributes nothing rather than widening the set.

    return frozenset(allowed) or None


# ── reading a country off a posting ──────────────────────────────────────────────────────────────
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

# Cities that identify a country on their own, beyond the config vocabulary ("Munich", "Barcelona").
_EXTRA_CITIES: dict[str, str] = {
    "munich": "DE", "münchen": "DE", "hamburg": "DE", "frankfurt": "DE", "cologne": "DE",
    "barcelona": "ES", "valencia": "ES", "milan": "IT", "rome": "IT", "krakow": "PL",
    "kraków": "PL", "wroclaw": "PL", "wrocław": "PL", "gdansk": "PL", "poznan": "PL",
    "brussels": "BE", "vienna": "AT", "prague": "CZ", "bratislava": "SK", "sofia": "BG",
    "bucharest": "RO", "athens": "GR", "helsinki": "FI", "oslo": "NO", "rotterdam": "NL",
    "utrecht": "NL", "eindhoven": "NL", "manchester": "GB", "edinburgh": "GB", "bristol": "GB",
    "cambridge": "GB", "new york": "US", "san francisco": "US", "austin": "US", "seattle": "US",
    "boston": "US", "chicago": "US", "toronto": "CA", "vancouver": "CA",
    # From the location strings actually in the corpus, ranked by rows each accounted for.
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
    # US cities: LinkedIn's "City, ST" strings, the largest block of unresolved rows before this.
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
    # The accented forms boards actually write, beside the plain ones above.
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

# Every ISO alpha-2 code the name table above can produce.
_ISO_CODES: frozenset[str] = frozenset(_COUNTRY_NAMES.values()) | frozenset(_CITY_COUNTRY.values())

# The codes that mean two things, which is why the trailing-code rule refuses them.
_AMBIGUOUS_CODES: frozenset[str] = _US_STATES & _ISO_CODES

# A place, then a two-letter code, at the end of the string: "Toulouse, FR", "Atlanta, GA".
_TRAILING_CODE = re.compile(r",\s*([A-Za-z]{2})\s*$")

_PLACES: dict[str, str] = {**_EXTRA_CITIES, **_CITY_COUNTRY, **_COUNTRY_NAMES}

# One alternation, scanned once, rather than one regex per place scanned in turn.
_PLACE_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(name) for name in sorted(_PLACES, key=len, reverse=True))
    + r")(?![a-z0-9])")


def _place_in(haystack: str) -> str | None:
    """The country of the longest place name in the string, or `None`."""
    best: str | None = None
    longest = 0
    for match in _PLACE_RE.finditer(haystack):
        name = match.group(1)
        if len(name) > longest:
            best, longest = _PLACES[name], len(name)
    return best


def read_country(location: str | None, *, title: str | None = None) -> str | None:
    """The ISO country code a posting is in, or `None` when it does not say."""
    for text in (location, title):
        haystack = (text or "").casefold()
        if not haystack:
            continue
        found = _place_in(haystack)
        if found is not None:
            return found

    # Only after every name has failed.
    return _trailing_code(location)


def _trailing_code(location: str | None) -> str | None:
    """The country from a trailing two-letter code, or `None` when it cannot be read as one."""
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
    """Whether a posting in `country` may be shown to somebody who asked for `wanted`."""
    if wanted is None:
        return True
    if country is None:
        return True
    return country in wanted
