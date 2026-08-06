"""The country filter, which is the only cheap pre-filter this project has that reads a fact."""
from __future__ import annotations

import pytest

from libs.corpus.geo import EU_COUNTRIES, allows, read_country, wanted_countries

# ── the trick: what a region selection actually admits ───────────────────────────────────────────

def test_europe_means_the_union_and_the_uk_is_not_in_it():
    """The case the owner raised, and the reason this module exists."""
    assert "GB" not in EU_COUNTRIES
    eu = wanted_countries(["europe"])
    assert eu is not None
    assert "GB" not in eu
    assert {"DE", "HU", "IE", "PL"} <= eu
    assert len(eu) == 27, "the union has 27 members; a wrong count here is a wrongly excluded user"


def test_a_non_eu_european_country_has_to_be_named():
    """Switzerland and Norway are in Europe and not in the union."""
    assert wanted_countries(["europe"]) is not None
    assert "CH" not in wanted_countries(["europe"])
    with_swiss = wanted_countries(["europe", "switzerland"])
    assert with_swiss is not None and "CH" in with_swiss
    assert with_swiss >= EU_COUNTRIES


def test_choosing_a_country_admits_that_country_and_no_other():
    """A user who said Germany means Germany."""
    assert wanted_countries(["united_kingdom"]) == frozenset({"GB"})
    germany_only = wanted_countries(["berlin"])
    assert germany_only == frozenset({"DE"})


def test_naming_a_city_admits_the_country_around_it():
    """Somebody who asked for Budapest has plainly not ruled out Hungary."""
    hungary = wanted_countries(["budapest"])
    assert hungary == frozenset({"HU"})
    assert allows("HU", hungary), "a Hungarian job must reach a user who asked for Budapest"


def test_selections_add_up_rather_than_narrowing_each_other():
    """Picking more places can only ever mean *more* jobs."""
    both = wanted_countries(["europe", "united_kingdom"])
    assert both is not None
    assert "GB" in both
    assert both >= EU_COUNTRIES


def test_remoteness_is_not_a_place_and_has_no_entry_here():
    """Where and how are two questions."""
    assert wanted_countries(["remote_eu"]) is None
    assert wanted_countries(["remote_worldwide"]) is None
    # And it cannot silently widen a real selection either.
    assert wanted_countries(["budapest", "remote_worldwide"]) == frozenset({"HU"})


def test_no_selection_at_all_is_not_a_filter():
    """A user who named no location has not excluded anywhere."""
    assert wanted_countries([]) is None
    assert wanted_countries(None) is None
    assert wanted_countries(["", "   "]) is None


def test_an_unknown_slug_does_not_quietly_widen_the_filter():
    """A vocabulary entry added without a country belongs in this module."""
    assert wanted_countries(["atlantis"]) is None, "nothing recognised means nothing to filter on"
    narrowed = wanted_countries(["budapest", "atlantis"])
    assert narrowed == frozenset({"HU"}), "the recognised half still applies"


# ── reading a country off a posting ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("location,expected", [
    ("Berlin, Germany", "DE"),
    ("London, United Kingdom", "GB"),
    ("London, UK", "GB"),
    ("Budapest, Hungary", "HU"),
    ("Remote - Netherlands", "NL"),
    ("Dublin, Ireland", "IE"),
    ("Kraków, Poland", "PL"),
    ("Zurich, Switzerland", "CH"),
    ("New York, NY, United States", "US"),
])
def test_a_country_is_read_from_the_way_boards_actually_write_it(location, expected):
    assert read_country(location) == expected


@pytest.mark.parametrize("location,expected", [
    ("Munich", "DE"),
    ("Barcelona", "ES"),
    ("Manchester", "GB"),
    ("Wrocław", "PL"),
])
def test_a_bare_city_still_identifies_its_country(location, expected):
    """Boards write "Munich" far more often than "Munich, Germany"."""
    assert read_country(location) == expected


@pytest.mark.parametrize("location", [
    "", None, "Remote", "Anywhere", "EMEA", "Multiple locations", "3 locations", "Hybrid",
])
def test_a_posting_that_names_no_country_reads_as_unknown(location):
    """Which is a pass, not a rejection — see the eligibility test below."""
    assert read_country(location) is None


def test_a_country_word_inside_another_word_is_not_a_country():
    """`us` fires inside "customer" and "Austria" contains "austri"."""
    assert read_country("Customer Success, Remote") is None
    assert read_country("Vienna, Austria") == "AT"
    assert read_country("Bustling office") is None


def test_the_location_outranks_the_title():
    """A title mentioning a country is a real signal."""
    assert read_country("Berlin, Germany", title="Backend Engineer (US hours)") == "DE"
    # With no location at all the title is better than nothing.
    assert read_country("", title="Backend Engineer, Germany") == "DE"
    assert read_country(None, title="Backend Engineer") is None


# ── the decision ─────────────────────────────────────────────────────────────────────────────────

def test_a_uk_job_never_reaches_a_user_who_asked_for_the_eu():
    """The whole entry, in one assertion."""
    eu = wanted_countries(["europe"])
    assert not allows("GB", eu)
    assert allows("DE", eu)


def test_a_job_with_no_readable_country_is_kept():
    """Absence of evidence is not evidence of absence."""
    assert allows(None, wanted_countries(["europe"]))
    assert allows(None, wanted_countries(["budapest"]))


def test_a_user_who_filtered_nothing_gets_everything():
    assert allows("US", None)
    assert allows(None, None)


def test_end_to_end_the_case_that_was_described():
    """A user searching the EU."""
    wanted = wanted_countries(["europe"])
    postings = [
        ("London, United Kingdom", False),
        ("Berlin, Germany", True),
        ("Remote", True),
        ("Warsaw, Poland", True),
        ("Austin, Texas", False),
    ]
    for location, should_pass in postings:
        assert allows(read_country(location), wanted) is should_pass, location


# ── the vocabulary and the filter have to agree ──────────────────────────────────────────────────

def test_every_location_a_user_can_pick_resolves_to_countries():
    """A slug the picker offers and the filter cannot read is a country filter that silently does not."""
    from libs.contracts.config import Location

    unresolved = [value.value for value in Location
                  if wanted_countries([value.value]) is None]
    assert not unresolved, (
        f"the picker offers {unresolved}, and `wanted_countries` cannot turn them into countries")


def test_remoteness_is_not_in_the_place_vocabulary():
    """Where and how are two questions. `remote_policy` is the second one, per section."""
    from libs.contracts.config import Location

    assert not [value.value for value in Location if value.value.startswith("remote")]


def test_a_stored_config_from_the_old_vocabulary_still_reads():
    """Migrated on read, not by rewriting rows."""
    from libs.contracts.config import migrate_config

    old = {"sections": [{"keywords": ["python"], "platform": "all",
                         "locations": ["european_union", "remote_eu", "remote_uk",
                                       "remote_worldwide", "budapest"]}]}
    locations = migrate_config(old)["sections"][0]["locations"]

    # `european_union` and `remote_eu` both mean the union, and fold into one entry.
    assert locations == ["europe", "united_kingdom", "budapest"]
    # And what comes out is something the filter can act on.
    wanted = wanted_countries(locations)
    assert wanted is not None and "GB" in wanted and "HU" in wanted


def test_the_v1_flat_config_still_migrates_and_its_locations_move_too():
    """Two migrations in one read: v1's flat shape becomes a section."""
    from libs.contracts.config import migrate_config

    migrated = migrate_config({"keywords": ["python"], "locations": ["european_union"]})
    assert migrated["sections"][0]["locations"] == ["europe"]


def test_the_picker_offers_exactly_what_the_schema_allows():
    """The frontend and the schema are two lists of the same thing, and they drifted."""
    import re
    from pathlib import Path

    from libs.contracts.config import Location

    source = (Path(__file__).resolve().parents[1]
              / "web" / "src" / "pages" / "onboarding" / "ConfigStep.tsx").read_text(
        encoding="utf-8")
    groups = re.search(r"const PLACE_GROUPS[^=]*=\s*\[(.*?)\n\];", source, re.S)
    assert groups, "PLACE_GROUPS not found — has the component been restructured?"
    offered = set(re.findall(r'"([a-z_]+)"', groups.group(1)))
    # Group labels and hints are prose, not slugs; only values that look like enum members count.
    allowed = {value.value for value in Location}

    assert offered - allowed == set(), (
        f"the picker offers places the server will refuse: {sorted(offered - allowed)}")
    assert allowed - offered == set(), (
        f"the schema allows places the picker never shows: {sorted(allowed - offered)}")


# ── reading the code beside a city (ROADMAP 6aj) ──────────────────────────────────────────────


def test_a_trailing_iso_code_places_a_city_we_do_not_know():
    """SmartRecruiters writes `City, CC` and a quarter of its rows were unplaceable without this."""
    assert read_country("Toulouse, FR") == "FR"
    assert read_country("Tallinn, EE") == "EE"
    assert read_country("Ho Chi Minh, VN") == "VN"
    assert read_country("Sofia, BG") == "BG"


def test_a_trailing_us_state_code_is_the_united_states():
    """LinkedIn's house style for American postings."""
    assert read_country("Springfield, MO") == "US"
    assert read_country("Overland Park, KS") == "US"
    assert read_country("Cedar Rapids, IA") == "US"
    assert read_country("Somewhere, NJ") == "US"


def test_a_code_that_means_two_things_is_refused_rather_than_guessed():
    """`Stuttgart, DE` is Germany and `Wilmington, DE` is Delaware."""
    from libs.corpus.geo import _AMBIGUOUS_CODES

    # Derived from the two tables rather than typed.
    assert {"DE", "IN", "CA", "CO", "MD"} <= _AMBIGUOUS_CODES

    # A city we do not know, beside a code that could be either: no answer.
    assert read_country("Nowheresville, DE") is None
    assert read_country("Nowheresville, IN") is None

    # The cost of that refusal, stated rather than hidden.
    assert read_country("Reutlingen, DE") is None

    # A code that belongs to only one of the two resolves normally, in both directions.
    assert read_country("Nowheresville, PA") == "US"     # Pennsylvania; no country uses PA here
    assert read_country("Nowheresville, HU") == "HU"     # Hungary; no US state uses HU


def test_a_known_city_settles_an_ambiguous_code():
    """Which is what makes refusing the ambiguous ones affordable."""
    assert read_country("Stuttgart, DE") == "DE"
    assert read_country("Bengaluru, IN") == "IN"
    assert read_country("Atlanta, GA") == "US"
    assert read_country("Philadelphia, PA") == "US"
    assert read_country("San Francisco, CA") == "US"


def test_the_longest_place_name_wins_not_the_earliest():
    """"US Remote - Berlin" is a Berlin job."""
    assert read_country("US Remote - Berlin") == "DE"
    assert read_country("Remote, Netherlands") == "NL"


def test_a_posting_that_names_no_place_stays_unknown():
    """Rule 2."""
    for nothing in ("", "   ", "Remote", "Remote job", "Worldwide", "Anywhere", "Homeoffice"):
        assert read_country(nothing) is None, nothing


def test_reading_a_country_is_fast_enough_for_ingest():
    """Every posting entering the corpus pays this, so it is a throughput number rather than a nicety."""
    import time

    samples = ["Toulouse, FR", "Berlin, Germany", "San Francisco, CA", "", "Remote",
               "Warszawa", "London, England, UK", "Nowheresville, DE"]
    started = time.perf_counter()
    for _ in range(2_000):
        for sample in samples:
            read_country(sample)
    per_call = (time.perf_counter() - started) / (2_000 * len(samples))

    assert per_call < 50e-6, f"{per_call * 1e6:.0f} microseconds per posting is too slow for ingest"
