"""Build `libs/corpus/places.tsv.gz`: every place name a posting may give, to its country.

    .venv/bin/python scripts/build_places.py

From GeoNames (geonames.org, CC BY 4.0): the cities of 15,000 people and more, by name and by ASCII
name, the local names of Europe's cities of 100,000 and more ("Napoli", "Warszawa"), and every
country's name. A name two places share goes to the European one, then to the larger: this is a
European job site, so "Valencia" and "Córdoba" are in Spain. Names under three letters are left out.
Run again only to take a newer GeoNames; the file it writes is committed.
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

from libs.corpus.geo import PLACES_FILE, _fold

SOURCE = "https://download.geonames.org/export/dump/"
LOCAL_NAMES_FROM = 100_000


def _latin(name: str) -> bool:
    """Written in the Latin alphabet, as a European board writes it, and not a code like "WAW"."""
    return not name.isupper() and all(
        char.isascii() or "LATIN" in unicodedata.name(char, "") for char in name if char.isalpha())


def main() -> None:
    def fetch(name: str) -> bytes:
        return httpx.get(SOURCE + name, timeout=120).raise_for_status().content

    countries = [line.split("\t") for line in fetch("countryInfo.txt").decode().splitlines()
                 if line and not line.startswith("#")]
    europe = {row[0] for row in countries if row[8] == "EU"}
    best: dict[str, tuple[bool, int, str]] = {}

    def offer(name: str, code: str, people: int) -> None:
        key = _fold(name)
        if len(key) >= 3:
            best[key] = max(best.get(key, (False, -1, "")), (code in europe, people, code))

    for row in countries:
        offer(row[4], row[0], 10**12)
    with zipfile.ZipFile(io.BytesIO(fetch("cities15000.zip"))) as archive:
        for line in archive.read("cities15000.txt").decode().splitlines():
            row = line.split("\t")
            code, people = row[8], int(row[14] or 0)
            names = {row[1], row[2]}
            if code in europe and people >= LOCAL_NAMES_FROM:
                names |= {name for name in row[3].split(",") if name and _latin(name)}
            for name in names:
                offer(name, code, people)

    lines = "".join(f"{name}\t{code}\n" for name, (_, _, code) in sorted(best.items()))
    PLACES_FILE.write_bytes(gzip.compress(lines.encode(), compresslevel=9, mtime=0))
    print(f"{len(best):,} names, {PLACES_FILE.stat().st_size:,} bytes -> {PLACES_FILE}")


if __name__ == "__main__":
    main()
