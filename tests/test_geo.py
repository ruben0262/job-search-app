"""The country filter, which is the only cheap pre-filter this project has that reads a fact."""
from __future__ import annotations

import pytest

from libs.corpus.geo import (
    EU_COUNTRIES,
    allows,
    continent_of,
    countries_in,
    posting_country,
    posting_place,
    read_country,
    wanted_countries,
)

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
    assert wanted_countries(["germany"]) == frozenset({"DE"})


def test_a_city_picked_before_cities_were_retired_reads_as_its_country():
    """My search picks countries only (owner, 2026-11-02). A city always admitted its whole country, so
    a config that still holds one reads as that country and nobody's jobs change."""
    from libs.contracts.config import migrate_config

    stored = {"sections": [{"keywords": ["nurse"], "locations": ["budapest", "hungary", "berlin", "zurich"]}]}
    locations = migrate_config(stored)["sections"][0]["locations"]

    assert locations == ["hungary", "germany", "switzerland"]
    assert wanted_countries(locations) == frozenset({"HU", "DE", "CH"})


@pytest.mark.parametrize(("location", "place"), [
    ("Berlin, Berlin, Germany", ("DE", "Berlin")),
    ("Warszawa, mazowieckie, Poland", ("PL", "Warsaw")),
    ("München, Bayern", ("DE", "Munich")),
    ("Greater London", ("GB", "London")),
    ("City Of London, England, UK", ("GB", "London")),
    ("Kraków (Hybrid)", ("PL", "Kraków")),
    ("Hybrid - Oulu, North Ostrobothnia", ("FI", "Oulu")),
    ("New York, NY", ("US", "New York City")),
    # Towns that were none of the 43 cities before 2026-11-24, so their jobs had no town.
    ("Debrecen, Hungary", ("HU", "Debrecen")),
    ("Obernai, Grand Est, France", ("FR", "Obernai")),
    ("Wernau (Neckar), DE", ("DE", "Wernau")),
    ("Vörstetten, Baden-Württemberg, Germany", ("DE", None)),
    ("Remote", (None, None)),
])
def test_a_postings_town_is_read_the_way_boards_write_it(location, place):
    """Every town of 5,000 people and more (owner, 2026-11-24: "keep a tab on ... all the cities as well
    from all the jobs"), where before a town was one of 43 cities or none."""
    assert posting_place(location) == place


@pytest.mark.parametrize(("location", "place"), [
    # Bordeaux is called "Porto" too, and is the larger; Belarus has the larger Brest.
    ("Porto, Portugal", ("PT", "Porto")),
    ("Brest, Brittany, France", ("FR", "Brest")),
    ("Surrey, England, United Kingdom", ("GB", None)),
    ("South Holland, Netherlands", ("NL", None)),
])
def test_a_town_is_the_one_of_its_name_in_the_postings_own_country(location, place):
    """Read with no regard to its country, "Porto, Portugal" was Bordeaux, "Surrey" a town in Canada and
    "South Holland" one in the United States (2026-11-24)."""
    assert posting_place(location) == place


@pytest.mark.parametrize(("location", "place"), [
    ("Luxembourg", ("LU", None)),
    ("Luxembourg, Luxembourg", ("LU", "Luxembourg")),
    ("Poland - Warsaw", ("PL", "Warsaw")),
    ("European Union", (None, None)),
    ("Remote job", (None, None)),
    ("Time zone: CET (+/- 3 hours)", (None, None)),
])
def test_a_country_or_a_word_is_never_a_town_that_shares_its_name(location, place):
    """A country's own name is the country, unless more of the place follows it; and only a whole part of a
    place is a town: read word by word, "European Union" was a town called Union, "Remote job" a village in
    France and "Time zone" one in Norway (2026-11-24)."""
    assert posting_place(location) == place


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
    assert wanted_countries(["hungary", "remote_worldwide"]) == frozenset({"HU"})


def test_no_selection_at_all_is_not_a_filter():
    """A user who named no location has not excluded anywhere."""
    assert wanted_countries([]) is None
    assert wanted_countries(None) is None
    assert wanted_countries(["", "   "]) is None


def test_an_unknown_slug_does_not_quietly_widen_the_filter():
    """A vocabulary entry added without a country belongs in this module."""
    assert wanted_countries(["atlantis"]) is None, "nothing recognised means nothing to filter on"
    narrowed = wanted_countries(["hungary", "atlantis"])
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
    assert posting_country(location) is None, "the world's towns do not turn a region into a country"


@pytest.mark.parametrize(("location", "expected"), [
    # From postings in the corpus that named no country we could read, 2026-11-02.
    ("Białystok", "PL"),
    ("Kielce", "PL"),
    ("Cracow", "PL"),
    ("Greater Bologna Metropolitan Area", "IT"),
    ("Greater La Coruña Area", "ES"),
    ("Cork Metropolitan Area", "IE"),
    ("Peru", "PE"),
    ("Hong Kong", "HK"),
    ("Ecuador, El Salvador, Guatemala, Honduras, Nicaragua", "EC"),
    ("APAC,  EMEA", None),
    # A name two places share is the European one, on a European job site.
    ("Valencia", "ES"),
    # And a code beside it still decides first.
    ("Toledo, OH", "US"),
])
def test_a_posting_that_names_only_its_city_is_still_in_a_country(location, expected):
    """Without its country, a posting passes every country filter: a job in Kielce reached somebody who
    asked for Ireland only (owner, 2026-11-02)."""
    assert posting_country(location) == expected


def test_a_city_anywhere_is_read_for_postings_only():
    """In a CV or a search word, "Nice" and "Split" are words, not places."""
    assert (posting_country("Split"), read_country("Split")) == ("HR", None)


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


def test_a_posting_in_a_city_an_alert_follows_is_read_as_that_city_by_every_name_it_goes_by():
    """An alert follows a city by its label (`libs.alerts.Rule`), so a posting there has to carry that label
    as its town, however its board writes it ("Köln", "Koeln", "Cologne"), or the alert hears of nothing.
    And a city whose country is not offered would retire into nothing."""
    from libs.corpus.geo import CITIES, CITY_COUNTRY, place_names

    unread = {name: posting_place(name) for slug, place in CITIES.items() for name in place_names(slug)
              if posting_place(name) != (next(iter(place.countries)), place.label)}
    assert not unread
    assert set(CITY_COUNTRY) == set(CITIES)


# A job's place as LinkedIn wrote it, for every country My search offers: from a page of each country's jobs
# listed on 2026-11-24, of which 1,088 of 1,089 were read in the country asked for and 944 in a town, Malta
# aside, then asked for by another name (CHANGELOG).
LINKEDIN = [
    ("Belfast, Northern Ireland, United Kingdom", "GB", "Belfast"),
    ("Dublin, County Dublin, Ireland", "IE", "Dublin"),
    ("Tirana, Tirana, Albania", "AL", "Tirana"),
    ("Andorra la Vella, Andorra", "AD", "Andorra la Vella"),
    ("Yerevan, Yerevan, Armenia", "AM", "Yerevan"),
    ("Vienna, Vienna, Austria", "AT", "Vienna"),
    ("Baku, Baku Ekonomic Zone, Azerbaijan", "AZ", "Baku"),
    ("Minsk, Minsk, Belarus", "BY", "Minsk"),
    ("Brussels, Brussels Region, Belgium", "BE", "Brussels"),
    ("Sarajevo, Federation of Bosnia and Herzegovina, Bosnia and Herzegovina", "BA", "Sarajevo"),
    ("Sofia, Sofia City, Bulgaria", "BG", "Sofia"),
    ("Zagreb, Zagreb, Croatia", "HR", "Zagreb"),
    ("Paphos, Cyprus", "CY", "Paphos"),
    ("Brno, South Moravia, Czechia", "CZ", "Brno"),
    ("Copenhagen, Capital Region of Denmark, Denmark", "DK", "Copenhagen"),
    ("Tallinn, Harjumaa, Estonia", "EE", "Tallinn"),
    ("Hósvík, Streymoy region, Faroe Islands", "FO", None),
    ("Helsinki, Uusimaa, Finland", "FI", "Helsinki"),
    ("Metz, Grand Est, France", "FR", "Metz"),
    ("Tbilisi, Georgia", "GE", "Tbilisi"),
    ("Munich, Bavaria, Germany", "DE", "Munich"),
    ("Gibraltar, Gibraltar", "GI", "Gibraltar"),
    ("Athens, Attiki, Greece", "GR", "Athens"),
    ("St Peter Port, Guernsey, Guernsey", "GG", "Saint Peter Port"),
    ("Budapest, Budapest, Hungary", "HU", "Budapest"),
    ("Reykjavík, Capital Region, Iceland", "IS", "Reykjavík"),
    ("Douglas, Douglas, Isle of Man", "IM", "Douglas"),
    ("Rome, Latium, Italy", "IT", "Rome"),
    ("St. Helier, St. Helier, Jersey", "JE", "Saint Helier"),
    ("Pristina, District of Pristina, Kosovo", "XK", "Pristina"),
    ("Riga, Riga, Latvia", "LV", "Riga"),
    ("Vaduz, Liechtenstein", "LI", "Vaduz"),
    ("Vilnius, Vilniaus, Lithuania", "LT", "Vilnius"),
    ("Luxembourg, Luxembourg, Luxembourg", "LU", "Luxembourg"),
    ("San Ġiljan, Saint Julian's, Malta", "MT", "San Ġiljan"),
    ("Chişinău, Chișinău, Moldova", "MD", "Chisinau"),
    ("Monaco, Monaco, Monaco", "MC", "Monaco"),
    ("Podgorica, Montenegro", "ME", "Podgorica"),
    ("Amsterdam, North Holland, Netherlands", "NL", "Amsterdam"),
    ("Skopje, Skopje Statistical Region, North Macedonia", "MK", "Skopje"),
    ("Oslo, Oslo, Norway", "NO", "Oslo"),
    ("Gdynia, Pomorskie, Poland", "PL", "Gdynia"),
    ("Porto, Porto, Portugal", "PT", "Porto"),
    ("Bucharest, Bucharest, Romania", "RO", "Bucharest"),
    ("Moscow City, Russia", "RU", "Moscow"),
    ("San Marino, San Marino", "SM", "San Marino"),
    ("Belgrade, Serbia", "RS", "Belgrade"),
    ("Bratislava, Bratislava, Slovakia", "SK", "Bratislava"),
    ("Ljubljana, Slovenia", "SI", "Ljubljana"),
    ("Málaga, Andalusia, Spain", "ES", "Málaga"),
    ("Stockholm, Stockholm County, Sweden", "SE", "Stockholm"),
    ("Zurich, Zurich, Switzerland", "CH", "Zurich"),
    ("Istanbul, Türkiye", "TR", "Istanbul"),
    ("Kyiv, Kyiv City, Ukraine", "UA", "Kyiv"),
    ("Vatican City", "VA", "Vatican City"),
    ("Mission Viejo, CA", "US", "Mission Viejo"),
    ("Winnipeg, Manitoba, Canada", "CA", "Winnipeg"),
]


@pytest.mark.parametrize(("location", "country", "town"), LINKEDIN)
def test_a_job_linkedin_lists_for_any_country_is_read_in_that_country_and_town(location, country, town):
    """Owner, 2026-11-24: "make sure all the countries are tested for linkedin ... same with cities too"."""
    assert posting_place(location, within=country) == (country, town)
    assert posting_place(location)[0] == country, "and from its place alone, the sweep's country aside"


def test_every_country_offered_has_a_place_linkedin_wrote_for_it_above():
    from libs.corpus.geo import COUNTRY_LABELS

    assert {country for _, country, _ in LINKEDIN} == set(COUNTRY_LABELS)


@pytest.mark.parametrize(("location", "title", "place"), [
    ("St. Gallen, Switzerland", None, ("CH", "Sankt Gallen")),
    ("Baku City, Baku Ekonomic Zone, Azerbaijan", None, ("AZ", "Baku")),
    # Its title named France, and the title was read before the town in its place.
    ("Vatican City", "Account Manager France", ("VA", "Vatican City")),
    ("Greater Nice Metropolitan Area", "Product Owner, Sofia Antipolis", ("FR", "Nice")),
    ("Richmond, Virginia", None, ("US", "Richmond")),
])
def test_a_place_is_read_before_the_title_and_as_a_board_shortens_or_wraps_it(location, title, place):
    assert posting_place(location, title=title) == place


@pytest.mark.parametrize(("location", "country"), [
    ("Polska", "PL"), ("Wrocław, Polska", "PL"), ("Spanien", "ES"), ("Cypern", "CY"),
    ("Deutschlandweit", "DE"), ("Texas - Remote", "US"), ("New Jersey", "US"), ("Atlanta, Georgia", "US"),
    ("Maine-et-Loire, France", "FR"),
])
def test_a_country_in_its_own_language_or_a_us_state_is_read_as_its_country(location, country):
    """Just Join IT's "Polska", Platsbanken's "Spanien", Lever's "Texas": read as no country, each job passed
    every country filter (2026-11-24)."""
    assert posting_place(location)[0] == country


def test_a_sweeps_country_decides_between_places_of_one_name_and_puts_nowhere_else_there():
    """In Serbia's list "Nis Metropolitan Area" is Niš, not Nice; in Russia's, a village called Kosovo is in
    Russia. A place that names nowhere in the country, "European Union" or a town in Wyoming, is left as it
    reads."""
    assert posting_place("Nis Metropolitan Area", within="RS") == ("RS", "Niš")
    assert posting_place("Nis Metropolitan Area") == ("FR", "Nice")
    assert posting_place("Kosovo, Tver\u2019, Russia", within="RU") == ("RU", "Tver")
    assert posting_place("European Union", within="DE") == (None, None)
    assert posting_place("Wheatland, WY", within="GG")[0] == "US"


def test_every_country_of_europe_can_be_picked():
    """Owner, 2026-11-24: "make sure that we have all the languages and countries, in my search so nothing is
    missed from europe". Only Åland and Svalbard are not, each a part of a country offered (Finland, Norway)
    with no job market of its own."""
    from libs.corpus.geo import COUNTRIES

    offered = {code for place in COUNTRIES.values() for code in place.countries}
    assert set(countries_in(["EU"])) - offered == {"AX", "SJ"}
    assert {"TR", "GE", "AM", "AZ", "CY"} <= set(countries_in(["EU"])), "in Europe and Asia both: in Europe"
    assert {continent_of(code) for code in offered} == {"EU", "NA"}


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
    assert locations == ["europe", "united_kingdom", "hungary"]
    # And what comes out is something the filter can act on.
    wanted = wanted_countries(locations)
    assert wanted is not None and "GB" in wanted and "HU" in wanted


def test_the_v1_flat_config_still_migrates_and_its_locations_move_too():
    """Two migrations in one read: v1's flat shape becomes a section."""
    from libs.contracts.config import migrate_config

    migrated = migrate_config({"keywords": ["python"], "locations": ["european_union"]})
    assert migrated["sections"][0]["locations"] == ["europe"]


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
