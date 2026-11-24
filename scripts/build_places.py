"""Build `libs/corpus/places.tsv.gz` and `libs/corpus/countries.tsv` from GeoNames.

    .venv/bin/python scripts/build_places.py

`places.tsv.gz` is every place name a posting may give, each with every place it can be, best first: a line
`name<TAB>country<TAB>town`, the town empty where the name is the country's own. From GeoNames
(geonames.org, CC BY 4.0): the towns of 5,000 people and more by their own name and its ASCII spelling, the
other names of Europe's towns of 100,000 and more and of the world's of a million ("Warszawa", "New York"),
and every country's name. Of the places one name can be, a European one comes first, then the larger, then
one called by its own name: a posting that names no country is in the first ("Hanover" is Hannover's, not a
town in the US), and one that does is in the first in its country (`libs.corpus.geo.posting_place`), so
"Porto, Portugal" is Porto although Bordeaux, larger, is called "Porto" too. A town of `CITIES` is named as
`CITIES` names it ("Cologne", not "Köln"), so an alert for it and a posting in it say the same. Names under
three letters are left out.

`countries.tsv` is every country's code, continent and name (`code<TAB>continent<TAB>name`). The countries
that lie in Europe and Asia both are counted in Europe, as the site's own list of places counts them.

Run again only to take a newer GeoNames; the files it writes are committed.
"""
from __future__ import annotations

import gzip
import io
import sys
import unicodedata
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libs.corpus.geo import CITIES, COUNTRIES_FILE, PLACES_FILE, _fold

SOURCE = "https://download.geonames.org/export/dump/"
TOWNS = "cities5000"
# Other names are taken for the towns this large: in Europe, and anywhere.
OTHER_NAMES_EUROPE = 100_000
OTHER_NAMES_ANYWHERE = 1_000_000
# In Europe and Asia both: in Europe, where My search lists them (`libs.corpus.geo.COUNTRIES`).
EUROPE_TOO = {"AM", "AZ", "CY", "GE", "TR"}
# Retired codes GeoNames still lists: Serbia and Montenegro, the Netherlands Antilles.
RETIRED = {"CS", "AN"}


def _latin(name: str) -> bool:
    """Written in the Latin alphabet, as a European board writes it, and not a code like "WAW"."""
    return not name.isupper() and all(
        char.isascii() or "LATIN" in unicodedata.name(char, "") for char in name if char.isalpha())


def main() -> None:
    def fetch(name: str) -> bytes:
        return httpx.get(SOURCE + name, timeout=120).raise_for_status().content

    countries = [line.split("\t") for line in fetch("countryInfo.txt").decode().splitlines()
                 if line and not line.startswith("#") and line.split("\t")[0] not in RETIRED]
    continent = {row[0]: "EU" if row[0] in EUROPE_TOO else row[8] for row in countries}
    # Each name's places, and how good a reading each is: (in Europe, people, its own name).
    found: dict[str, dict[tuple[str, str], tuple[bool, int, bool]]] = {}

    def offer(name: str, code: str, town: str, rank: tuple[bool, int, bool]) -> None:
        key = _fold(name)
        if len(key) >= 3:
            held = found.setdefault(key, {})
            held[(code, town)] = max(held.get((code, town), rank), rank)

    for row in countries:
        offer(row[4], row[0], "", (continent[row[0]] == "EU", 10**12, True))
    with zipfile.ZipFile(io.BytesIO(fetch(f"{TOWNS}.zip"))) as archive:
        for line in archive.read(f"{TOWNS}.txt").decode().splitlines():
            row = line.split("\t")
            code, people, own = row[8], int(row[14] or 0), {row[1], row[2]}
            if code not in continent:
                continue
            european = continent[code] == "EU"
            other = set()
            if people >= (OTHER_NAMES_EUROPE if european else OTHER_NAMES_ANYWHERE):
                other = {name for name in row[3].split(",") if name and _latin(name)} - own
            for name in own | other:
                offer(name, code, row[1], (european, people, name in own))

    # A town of `CITIES` under the name the site gives it: the place its own name reads as, in its country.
    renamed = {}
    for place in CITIES.values():
        (code,) = place.countries
        held = found.get(_fold(place.label), {})
        town = next((town for (each, town), _ in sorted(held.items(), key=lambda item: item[1], reverse=True)
                     if each == code and town), None)
        if town is None:
            raise SystemExit(f"{place.label} is not a town of {code} in {TOWNS}")
        renamed[(code, town)] = place.label

    lines = []
    for key in sorted(found):
        for (code, town), _ in sorted(found[key].items(), key=lambda item: item[1], reverse=True):
            lines.append(f"{key}\t{code}\t{renamed.get((code, town), town)}\n")
    PLACES_FILE.write_bytes(gzip.compress("".join(lines).encode(), compresslevel=9, mtime=0))
    COUNTRIES_FILE.write_text("".join(f"{row[0]}\t{continent[row[0]]}\t{row[4]}\n"
                                      for row in sorted(countries)), encoding="utf-8")
    size = PLACES_FILE.stat().st_size
    print(f"{len(found):,} names, {len(lines):,} places, {size:,} bytes -> {PLACES_FILE}")
    print(f"{len(countries)} countries -> {COUNTRIES_FILE}")


if __name__ == "__main__":
    main()
