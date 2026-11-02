"""What country a posting is in, and which countries a user has actually asked for."""
from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# ISO 3166-1 alpha-2: a two-letter code is stable, indexable and unambiguous where a name is not.

# The 27 member states, 2026.
EU_COUNTRIES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})

@dataclass(frozen=True)
class Place:
    """A place a person can pick: what it is called, the countries it covers, and how else it is written."""

    label: str
    countries: frozenset[str]
    # Other spellings, lower case, as postings and CVs write them.
    names: tuple[str, ...] = ()


def _one(code: str) -> frozenset[str]:
    return frozenset({code})


# The places on My search: countries only (owner, 2026-11-02). The slug is what a config stores, the
# label what the page shows and the job boards are asked for. The three people pick most come first.
COUNTRIES: dict[str, Place] = {
    "europe": Place("European Union", EU_COUNTRIES),
    "united_kingdom": Place("United Kingdom", _one("GB"), ("uk", "england", "scotland", "wales",
                                                           "great britain", "northern ireland")),
    "ireland": Place("Ireland", _one("IE")),
    "austria": Place("Austria", _one("AT"), ("österreich",)),
    "belgium": Place("Belgium", _one("BE")),
    "bulgaria": Place("Bulgaria", _one("BG")),
    "croatia": Place("Croatia", _one("HR")),
    "cyprus": Place("Cyprus", _one("CY")),
    "czechia": Place("Czechia", _one("CZ"), ("czech republic",)),
    "denmark": Place("Denmark", _one("DK")),
    "estonia": Place("Estonia", _one("EE")),
    "finland": Place("Finland", _one("FI")),
    "france": Place("France", _one("FR")),
    "germany": Place("Germany", _one("DE"), ("deutschland",)),
    "greece": Place("Greece", _one("GR")),
    "hungary": Place("Hungary", _one("HU"), ("magyarország",)),
    "iceland": Place("Iceland", _one("IS")),
    "italy": Place("Italy", _one("IT")),
    "latvia": Place("Latvia", _one("LV")),
    "lithuania": Place("Lithuania", _one("LT")),
    "luxembourg": Place("Luxembourg", _one("LU")),
    "malta": Place("Malta", _one("MT")),
    "netherlands": Place("Netherlands", _one("NL"), ("holland",)),
    "norway": Place("Norway", _one("NO")),
    "poland": Place("Poland", _one("PL")),
    "portugal": Place("Portugal", _one("PT")),
    "romania": Place("Romania", _one("RO")),
    "serbia": Place("Serbia", _one("RS")),
    "slovakia": Place("Slovakia", _one("SK")),
    "slovenia": Place("Slovenia", _one("SI")),
    "spain": Place("Spain", _one("ES")),
    "sweden": Place("Sweden", _one("SE")),
    "switzerland": Place("Switzerland", _one("CH"), ("schweiz", "suisse")),
    "ukraine": Place("Ukraine", _one("UA")),
    "united_states": Place("United States", _one("US"), ("usa",)),
    "canada": Place("Canada", _one("CA")),
}

# Cities a posting is read as being in, for the jobs page's city filter. Not picked on My search: a city
# somebody picked before 2026-11-02 is read as its country (`libs.contracts.config._migrate_locations`).
CITIES: dict[str, Place] = {
    "amsterdam": Place("Amsterdam", _one("NL")),
    "athens": Place("Athens", _one("GR")),
    "barcelona": Place("Barcelona", _one("ES")),
    "berlin": Place("Berlin", _one("DE")),
    "brussels": Place("Brussels", _one("BE"), ("bruxelles", "brussel")),
    "bucharest": Place("Bucharest", _one("RO"), ("bucurești", "bucuresti")),
    "budapest": Place("Budapest", _one("HU")),
    "cologne": Place("Cologne", _one("DE"), ("köln", "koeln")),
    "copenhagen": Place("Copenhagen", _one("DK"), ("københavn",)),
    "dublin": Place("Dublin", _one("IE")),
    "edinburgh": Place("Edinburgh", _one("GB")),
    "frankfurt": Place("Frankfurt", _one("DE")),
    "gdansk": Place("Gdańsk", _one("PL"), ("gdansk",)),
    "hamburg": Place("Hamburg", _one("DE")),
    "helsinki": Place("Helsinki", _one("FI")),
    "krakow": Place("Kraków", _one("PL"), ("krakow",)),
    "lisbon": Place("Lisbon", _one("PT"), ("lisboa",)),
    "london": Place("London", _one("GB")),
    "lyon": Place("Lyon", _one("FR")),
    "madrid": Place("Madrid", _one("ES")),
    "manchester": Place("Manchester", _one("GB")),
    "milan": Place("Milan", _one("IT"), ("milano",)),
    "munich": Place("Munich", _one("DE"), ("münchen", "muenchen")),
    "oslo": Place("Oslo", _one("NO")),
    "paris": Place("Paris", _one("FR")),
    "porto": Place("Porto", _one("PT")),
    "prague": Place("Prague", _one("CZ"), ("praha",)),
    "riga": Place("Riga", _one("LV")),
    "rome": Place("Rome", _one("IT"), ("roma",)),
    "rotterdam": Place("Rotterdam", _one("NL")),
    "sofia": Place("Sofia", _one("BG")),
    "stockholm": Place("Stockholm", _one("SE")),
    "stuttgart": Place("Stuttgart", _one("DE")),
    "tallinn": Place("Tallinn", _one("EE")),
    "the_hague": Place("The Hague", _one("NL"), ("den haag",)),
    "utrecht": Place("Utrecht", _one("NL")),
    "valencia": Place("Valencia", _one("ES")),
    "vienna": Place("Vienna", _one("AT"), ("wien",)),
    "vilnius": Place("Vilnius", _one("LT")),
    "warsaw": Place("Warsaw", _one("PL"), ("warszawa",)),
    "wroclaw": Place("Wrocław", _one("PL"), ("wroclaw",)),
    "zagreb": Place("Zagreb", _one("HR")),
    "zurich": Place("Zurich", _one("CH"), ("zürich",)),
}

# A country's name from its code, for a filter that lists the countries jobs are in.
COUNTRY_LABELS: dict[str, str] = {next(iter(place.countries)): place.label
                                  for place in COUNTRIES.values() if len(place.countries) == 1}

# The country each city is in, by slug: what a city picked before 2026-11-02 now reads as.
CITY_COUNTRY: dict[str, str] = {
    city: next(slug for slug, country in COUNTRIES.items() if country.countries == place.countries)
    for city, place in CITIES.items()}


def place_names(slug: str) -> tuple[str, ...]:
    """Every way a country or a city is written, lower case: its label first."""
    place = COUNTRIES.get(slug) or CITIES[slug]
    return (place.label.casefold(), *place.names)


def wanted_countries(locations: list[str] | tuple[str, ...] | None) -> frozenset[str] | None:
    """The countries a person picked, as ISO codes, or `None` when they picked none."""
    allowed = {code for raw in locations or () if (place := COUNTRIES.get(str(raw or "").strip().lower()))
               for code in place.countries}
    return frozenset(allowed) or None


_CITY_NAMES: dict[str, str] = {name: slug for slug in CITIES for name in place_names(slug)}
_CITY_RE = re.compile(r"(?<!\w)(" + "|".join(map(re.escape, sorted(_CITY_NAMES, key=len, reverse=True)))
                      + r")(?!\w)")


def read_city(location: str | None) -> str | None:
    """The city a posting is in, as a picked place names it, or `None` when it is none of them."""
    found = _CITY_RE.search((location or "").casefold())
    return _CITY_NAMES[found.group(1)] if found else None


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

# The picked cities, each name to its country, for reading a posting's country as well as its city.
_CITY_COUNTRY: dict[str, str] = {name: next(iter(CITIES[slug].countries))
                                 for name, slug in _CITY_NAMES.items()}

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


# ── the last resort for a posting: every city in the world ────────────────────────────────────────
# Most postings name their country, or a city the tables above know. The rest name a city of their own
# ("Białystok", "Greater Bologna Metropolitan Area") and would pass every country filter unread (owner,
# 2026-11-02). `places.tsv.gz` is GeoNames' cities of 15,000 people and more, and every country's name,
# built by `scripts/build_places.py`. Read for postings only: in a CV or a search word, "Nice", "Split"
# and "Mobile" are words, not places.
PLACES_FILE = Path(__file__).with_name("places.tsv.gz")

# What a board wraps a place in: LinkedIn's "Greater Venice Metropolitan Area", "Cork Area".
_DECORATION = re.compile(r"^greater\s+|\s+(?:metropolitan\s+)?(?:area|region)$")


def _fold(text: str) -> str:
    """Lower case, accents off, as `places.tsv.gz` is written."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


@cache
def _world() -> dict[str, str]:
    """Every place name in the file, to its country. Read once, the first time a posting needs it."""
    with gzip.open(PLACES_FILE, "rt", encoding="utf-8") as handle:
        return dict(line.rstrip("\n").split("\t") for line in handle)


def _world_country(location: str | None) -> str | None:
    """The country of the first part of `location` that is a place anywhere, or `None`."""
    for piece in re.split(r"[,;/|()]", location or ""):
        part = piece.strip()
        # "APAC", "EMEA": a region in capitals, never a town that shares its letters.
        if not part or (part.isupper() and len(part) <= 5):
            continue
        if found := _world().get(_DECORATION.sub("", _fold(part))):
            return found
    return None


def posting_country(location: str | None, *, title: str | None = None) -> str | None:
    """The ISO country code a posting is in: `read_country`, then any city in the world."""
    return read_country(location, title=title) or _world_country(location)


def allows(country: str | None, wanted: frozenset[str] | None) -> bool:
    """Whether a posting in `country` may be shown to somebody who asked for `wanted`."""
    if wanted is None:
        return True
    if country is None:
        return True
    return country in wanted
