"""What country a posting is in, and which countries a user has actually asked for."""
from __future__ import annotations

import gzip
import re
import unicodedata
from array import array
from bisect import bisect_left
from dataclasses import dataclass
from functools import cache, lru_cache
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


# The places on My search: countries only (owner, 2026-11-02), every one in Europe (owner, 2026-11-24: "so
# nothing is missed from europe"), those in Europe and Asia both among them. The slug is what a config stores,
# the label what the page shows and the job boards are asked for (LinkedIn's own name: "Türkiye"). The three
# people pick most come first, then Europe A to Z, then the two across the Atlantic.
COUNTRIES: dict[str, Place] = {
    "europe": Place("European Union", EU_COUNTRIES),
    "united_kingdom": Place("United Kingdom", _one("GB"), ("uk", "england", "scotland", "wales",
                                                           "great britain", "northern ireland")),
    "ireland": Place("Ireland", _one("IE")),
    "albania": Place("Albania", _one("AL")),
    "andorra": Place("Andorra", _one("AD")),
    "armenia": Place("Armenia", _one("AM")),
    "austria": Place("Austria", _one("AT"), ("österreich",)),
    "azerbaijan": Place("Azerbaijan", _one("AZ")),
    "belarus": Place("Belarus", _one("BY")),
    "belgium": Place("Belgium", _one("BE")),
    "bosnia_and_herzegovina": Place("Bosnia and Herzegovina", _one("BA"), ("bosnia",)),
    "bulgaria": Place("Bulgaria", _one("BG")),
    "croatia": Place("Croatia", _one("HR")),
    "cyprus": Place("Cyprus", _one("CY")),
    "czechia": Place("Czechia", _one("CZ"), ("czech republic",)),
    "denmark": Place("Denmark", _one("DK")),
    "estonia": Place("Estonia", _one("EE")),
    "faroe_islands": Place("Faroe Islands", _one("FO")),
    "finland": Place("Finland", _one("FI")),
    "france": Place("France", _one("FR")),
    "georgia": Place("Georgia", _one("GE")),
    "germany": Place("Germany", _one("DE"), ("deutschland",)),
    "gibraltar": Place("Gibraltar", _one("GI")),
    "greece": Place("Greece", _one("GR")),
    "guernsey": Place("Guernsey", _one("GG")),
    "hungary": Place("Hungary", _one("HU"), ("magyarország",)),
    "iceland": Place("Iceland", _one("IS")),
    "isle_of_man": Place("Isle of Man", _one("IM")),
    "italy": Place("Italy", _one("IT")),
    "jersey": Place("Jersey", _one("JE")),
    "kosovo": Place("Kosovo", _one("XK")),
    "latvia": Place("Latvia", _one("LV")),
    "liechtenstein": Place("Liechtenstein", _one("LI")),
    "lithuania": Place("Lithuania", _one("LT")),
    "luxembourg": Place("Luxembourg", _one("LU")),
    "malta": Place("Malta", _one("MT")),
    "moldova": Place("Moldova", _one("MD")),
    "monaco": Place("Monaco", _one("MC")),
    "montenegro": Place("Montenegro", _one("ME")),
    "netherlands": Place("Netherlands", _one("NL"), ("holland",)),
    "north_macedonia": Place("North Macedonia", _one("MK"), ("macedonia",)),
    "norway": Place("Norway", _one("NO")),
    "poland": Place("Poland", _one("PL")),
    "portugal": Place("Portugal", _one("PT")),
    "romania": Place("Romania", _one("RO")),
    "russia": Place("Russia", _one("RU"), ("russian federation",)),
    "san_marino": Place("San Marino", _one("SM")),
    "serbia": Place("Serbia", _one("RS")),
    "slovakia": Place("Slovakia", _one("SK")),
    "slovenia": Place("Slovenia", _one("SI")),
    "spain": Place("Spain", _one("ES")),
    "sweden": Place("Sweden", _one("SE")),
    "switzerland": Place("Switzerland", _one("CH"), ("schweiz", "suisse")),
    "turkey": Place("Türkiye", _one("TR"), ("turkey", "turkiye")),
    "ukraine": Place("Ukraine", _one("UA")),
    "vatican_city": Place("Vatican City", _one("VA"), ("holy see",)),
    # "New Jersey" is in a CV's address far more often than Jersey is (`libs.documents.read`).
    "united_states": Place("United States", _one("US"), ("usa", "new jersey")),
    "canada": Place("Canada", _one("CA")),
}

# The cities an alert can follow and a CV's address is read for. Not picked on My search: a city somebody
# picked before 2026-11-02 is read as its country (`libs.contracts.config._migrate_locations`). A posting in
# one carries its label as its town (`posting_place`), which `places.tsv.gz` is built to give.
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
    # The rest of Europe, as My search has it since 2026-11-24. Not "jersey", which is New Jersey's too, nor
    # Azerbaijan or the Vatican, whose codes are Arizona's and Virginia's: a code read here is refused at the
    # end of a place ("Wallops Island, VA"), and `places.tsv.gz` names all three.
    "türkiye": "TR", "turkiye": "TR", "bosnia and herzegovina": "BA", "andorra": "AD",
    "liechtenstein": "LI", "monaco": "MC", "san marino": "SM", "gibraltar": "GI", "guernsey": "GG",
    "isle of man": "IM", "faroe islands": "FO", "russian federation": "RU", "republic of moldova": "MD",
    # Each country of Europe in its own language, and as the boards that wrote it in another did (Just Join
    # IT's "Polska", Platsbanken's "Spanien" and "Cypern", Arbeitnow's "Deutschlandweit"), read as nothing
    # before 2026-11-24, so every country filter let the job through. Not "island" (Iceland in German and
    # Swedish), a word of countless places.
    "polska": "PL", "polen": "PL", "česko": "CZ", "česká republika": "CZ", "tschechien": "CZ",
    "tjeckien": "CZ",
    "slovensko": "SK", "österreich": "AT", "österrike": "AT", "suisse": "CH", "svizzera": "CH",
    "sverige": "SE", "schweden": "SE", "norge": "NO", "norwegen": "NO", "danmark": "DK", "dänemark": "DK",
    "suomi": "FI", "finnland": "FI", "españa": "ES", "espana": "ES", "spanien": "ES", "italia": "IT",
    "italien": "IT", "nederland": "NL", "niederlande": "NL", "nederländerna": "NL", "belgië": "BE",
    "belgique": "BE", "belgien": "BE", "éire": "IE", "irland": "IE", "ελλάδα": "GR", "griechenland": "GR",
    "grekland": "GR", "românia": "RO", "rumänien": "RO", "българия": "BG", "hrvatska": "HR", "kroatien": "HR",
    "slovenija": "SI", "srbija": "RS", "eesti": "EE", "estland": "EE", "latvija": "LV", "lettland": "LV",
    "lietuva": "LT", "litauen": "LT", "shqipëria": "AL", "crna gora": "ME", "bosna i hercegovina": "BA",
    "sakartvelo": "GE", "україна": "UA", "ukraina": "UA", "россия": "RU", "kypros": "CY", "cypern": "CY",
    "zypern": "CY", "lëtzebuerg": "LU", "luxemburg": "LU", "ungarn": "HU", "ungern": "HU", "tyskland": "DE",
    "frankrike": "FR", "frankreich": "FR", "storbritannien": "GB", "großbritannien": "GB",
    "grossbritannien": "GB", "deutschlandweit": "DE",
    # The United States' states, which a board writes for the country ("Texas - Remote", "New Jersey"):
    # unread, their jobs passed every country filter too. Not Georgia, a country of its own, nor Maine, which
    # "Maine-et-Loire" in France would match.
    **dict.fromkeys((
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
        "delaware", "florida", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
        "kentucky", "louisiana", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
        "new mexico", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
        "utah", "vermont", "virginia", "west virginia", "wisconsin", "wyoming"), "US"),
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

# The cities of `CITIES`, each name to its country, for reading a posting's country.
_CITY_COUNTRY: dict[str, str] = {name: next(iter(place.countries))
                                 for slug, place in CITIES.items() for name in place_names(slug)}

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
    """The ISO country code a posting is in, by the names and codes above, or `None` when it does not say."""
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


# ── the last resort for a posting, and its town: every town in the world ─────────────────────────
# Most postings name their country, or a city the tables above know. The rest name a town of their own
# ("Białystok", "Greater Bologna Metropolitan Area") and would pass every country filter unread (owner,
# 2026-11-02). And every posting's town is kept (owner, 2026-11-24: "keep a tab on all the countries more
# importantly all the cities as well from all the jobs"), where before it was one of `CITIES` or none: half
# the jobs had none. `places.tsv.gz` is GeoNames' towns of 5,000 people and more, and every country's name,
# built by `scripts/build_places.py`; `countries.tsv` every country's continent and name. Read for postings
# only: in a CV or a search word, "Nice", "Split" and "Mobile" are words, not places.
PLACES_FILE = Path(__file__).with_name("places.tsv.gz")
COUNTRIES_FILE = Path(__file__).with_name("countries.tsv")

# What a board wraps a place in: LinkedIn's "Greater Venice Metropolitan Area", "Cork Area", "City Of London".
_DECORATION = re.compile(r"^(?:greater|city of)\s+|\s+(?:metropolitan\s+)?(?:area|region)$")
# Where one part of a place ends and the next begins: "Kraków (Hybrid)", "Remote - Oslo", "Paris / London".
_PLACE_PARTS = re.compile(r"[,;/|()]|\s[-\u2013\u2014]\s")

# The continents, by the code `countries.tsv` gives each country's.
CONTINENTS: dict[str, str] = {"EU": "Europe", "AS": "Asia", "AF": "Africa", "NA": "North America",
                              "SA": "South America", "OC": "Oceania", "AN": "Antarctica"}


def _fold(text: str) -> str:
    """Lower case, accents off, as `places.tsv.gz` is written."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


@cache
def _file() -> tuple[bytes, array]:
    """`places.tsv.gz` as it is written, a place a line in the order of their names, and where each line
    starts, read once, the first time a posting needs it: 2.8 MB, in 0.1 s. Kept so, not as a table of names,
    which held 26 MB in every process that stores postings, the harvester among them (owner, 2026-11-24: "we
    don't consume too much memory"); a posting is read in 60 µs where the table took 20."""
    with gzip.open(PLACES_FILE, "rb") as handle:
        text = handle.read()
    return text, array("I", [0, *(found.end() for found in re.finditer(b"\n", text))][:-1])


@lru_cache(maxsize=8192)
def _places(name: str) -> tuple[tuple[str, str], ...]:
    """Every place `name` can be, best first: (country, town), the town empty where the name is the country's
    own. Found by halving the file, whose lines are in the order of their names, as UTF-8 keeps them; a name
    asked for again, as "Warsaw" and "Poland" are all day, is answered from what was found."""
    text, starts = _file()

    def named(line: int) -> bytes:
        return text[starts[line]:text.index(b"\t", starts[line])]

    wanted, line, found = name.encode(), 0, []
    line = bisect_left(range(len(starts)), wanted, key=named)
    while line < len(starts) and named(line) == wanted:
        _, code, town = text[starts[line]:text.index(b"\n", starts[line])].decode().split("\t")
        found.append((code, town))
        line += 1
    return tuple(found)


@cache
def _countries() -> dict[str, tuple[str, str]]:
    """Every country's continent and name, by its code."""
    rows = (line.split("\t") for line in COUNTRIES_FILE.read_text(encoding="utf-8").splitlines())
    return {code: (continent, name) for code, continent, name in rows}


def continent_of(code: str | None) -> str | None:
    """The continent a country is in (`CONTINENTS`), or `None` for a code that is no country."""
    return _countries().get(code or "", (None, ""))[0]


def countries_in(continents: list[str] | tuple[str, ...]) -> list[str]:
    """Every country in these continents, as ISO codes."""
    return sorted(code for code, (continent, _) in _countries().items() if continent in continents)


def country_name(code: str) -> str:
    """A country's name: as My search names it, or as GeoNames does, or its code for one neither knows."""
    return COUNTRY_LABELS.get(code) or _countries().get(code, ("", code))[1]


def _parts(location: str | None) -> list[str]:
    """The parts of a place, in order, each as `places.tsv.gz` is written; none that is a region in capitals
    ("APAC", "EMEA"), never a town that shares its letters."""
    return [_DECORATION.sub("", _fold(part)) for piece in _PLACE_PARTS.split(location or "")
            if (part := piece.strip()) and not (part.isupper() and len(part) <= 5)]


def _named(part: str) -> tuple[tuple[str, str], ...]:
    """The places one part of a place can be, as written first, then as a board shortens or wraps its name:
    "St. Helier" is Saint Helier as well as a district of London, "St. Gallen" Sankt Gallen, LinkedIn's
    "Moscow City" Moscow (2026-11-24)."""
    names = (part, *(re.sub(r"^st\.?\s+", saint, part) for saint in ("saint ", "sankt ")),
             re.sub(r"\s+city$", "", part))
    return tuple(dict.fromkeys(place for name in dict.fromkeys(names) for place in _places(name)))


def _world_country(location: str | None) -> str | None:
    """The country `location` names outright anywhere in it, or else the country of its first part that is a
    place anywhere, or `None`: "St. Helier, Jersey" is in Jersey, however many towns are called St Helier."""
    named = [places for part in _parts(location) if (places := _named(part))]
    return next((code for places in named for code, town in places if not town),
                named[0][0][0] if named else None)


def posting_country(location: str | None, *, title: str | None = None) -> str | None:
    """The ISO country code a posting is in: what its place says, by name or code (`read_country`), then by
    any town in the world; and only then its title. Its title came before its place's town, and a job in
    "Vatican City" whose title named France was in France (2026-11-24)."""
    return read_country(location) or _world_country(location) or read_country(title)


def posting_place(location: str | None, *, title: str | None = None,
                  within: str | None = None) -> tuple[str | None, str | None]:
    """The country a posting is in (`posting_country`) and its town, each `None` where it does not say: the
    first part of its place that names a town in that country ("Kraków (Hybrid)", "Porto, Portugal"), by the
    name `places.tsv.gz` gives it ("Warszawa" is Warsaw). A part that is a country's own name is that country,
    unless more follows it ("Luxembourg, Luxembourg"). Only a whole part is read: read word by word, "New
    York" was York, "European Union" a town called Union and "Remote job" a village in France (2026-11-24).

    `within` is the country the board itself put the job in, for a board that lists a country at a time
    (LinkedIn's sweep of one): the job is there wherever its place names somewhere in it, a name it shares
    with another country's town or a village that shares a country's name. In Serbia's list "Nis Metropolitan
    Area" is Niš, not Nice; in Russia's, "Kosovo, Tver', Russia" is in Russia (2026-11-24). A place that names
    nowhere in it ("European Union", "Remote") is not put there."""
    country = posting_country(location, title=title)
    parts = _parts(location)
    if within and country != within and any(code == within for part in parts for code, _ in _named(part)):
        country = within
    for index, part in enumerate(parts):
        # A country's own name as written, not as unwrapped: "Vatican City" is a town as well as "Vatican".
        if any(not town for _, town in _places(part)) and not (index == 0 and len(parts) > 1):
            continue
        if town := next((town for code, town in _named(part) if town and code == country), None):
            return country, town
    return country, None


def allows(country: str | None, wanted: frozenset[str] | None) -> bool:
    """Whether a posting in `country` may be shown to somebody who asked for `wanted`."""
    if wanted is None:
        return True
    if country is None:
        return True
    return country in wanted
