"""The country filter, which is the only cheap pre-filter this project has that reads a fact.

**Tested hard because the failure is invisible.** A filter that is too tight costs somebody a job
they would have taken, and nothing in the product reports a posting that was never scored — no
error, no count, no alert. That is the same shape as the incident that zeroed eleven sources for
six hours, and the reason this file is longer than the module it tests.
"""
from __future__ import annotations

import pytest

from libs.corpus.geo import EU_COUNTRIES, allows, read_country, wanted_countries

# ── the trick: what a region selection actually admits ───────────────────────────────────────────

def test_europe_means_the_union_and_the_uk_is_not_in_it():
    """**The case the owner raised, and the reason this module exists.**

    A user searching Europe who receives London jobs is being shown work they cannot take, and no
    ranking signal repairs a hard eligibility fact. Nothing enforced this before: the rules score
    awarded full marks to any posting once a region was selected, on the stated grounds that no
    posting spells the region's name in its location field.

    **One slug, and it carries the union.** There were two — `european_union` and `europe` — a
    distinction nobody reading a checkbox would draw correctly, and getting it wrong silently
    changes which jobs somebody sees.
    """
    assert "GB" not in EU_COUNTRIES
    eu = wanted_countries(["europe"])
    assert eu is not None
    assert "GB" not in eu
    assert {"DE", "HU", "IE", "PL"} <= eu
    assert len(eu) == 27, "the union has 27 members; a wrong count here is a wrongly excluded user"


def test_a_non_eu_european_country_has_to_be_named():
    """Switzerland and Norway are in Europe and not in the union. Under one slug that means naming
    them, which is the honest cost of not having two words nobody can tell apart."""
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
    """Somebody who asked for Budapest has plainly not ruled out Hungary — and a posting in Hungary
    rarely writes the city we happened to put in a picker."""
    hungary = wanted_countries(["budapest"])
    assert hungary == frozenset({"HU"})
    assert allows("HU", hungary), "a Hungarian job must reach a user who asked for Budapest"


def test_selections_add_up_rather_than_narrowing_each_other():
    """Picking more places can only ever mean *more* jobs. A user adding the UK to an EU search has
    widened it, and an intersection here would empty their feed."""
    both = wanted_countries(["europe", "united_kingdom"])
    assert both is not None
    assert "GB" in both
    assert both >= EU_COUNTRIES


def test_remoteness_is_not_a_place_and_has_no_entry_here():
    """**Where and how are two questions.** The vocabulary carried `remote_eu`, `remote_uk`,
    `remote_us` and `remote_worldwide`, which made them one question with answers that could
    contradict each other — and duplicated a `remote_policy` field the config already had.

    Working arrangement is `libs.corpus.workfilter`'s job. This module answers only which country,
    so a remote slug is an unrecognised one and contributes nothing.
    """
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
    """A vocabulary entry added without a country belongs in this module. Failing open on it would
    let a typo disable somebody's country filter with no symptom."""
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
    """Which is a **pass**, not a rejection — see the eligibility test below. Remote roles name no
    country on purpose, and they are the most portable jobs on the board."""
    assert read_country(location) is None


def test_a_country_word_inside_another_word_is_not_a_country():
    """`us` fires inside "customer" and "Austria" contains "austri"; without word boundaries this
    filter is a random number generator. This is the test that keeps it a filter."""
    assert read_country("Customer Success, Remote") is None
    assert read_country("Vienna, Austria") == "AT"
    assert read_country("Bustling office") is None


def test_the_location_outranks_the_title():
    """A title mentioning a country is a real signal, but a location string is what a board *means*
    by where the job is. Preferring the title would let "US-style benefits" relocate a Berlin job."""
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
    """**Absence of evidence is not evidence of absence**, which is the rule `workfilter` and
    `_location_points` already follow. Rejecting these would discard exactly the remote roles that
    suit somebody searching across a region."""
    assert allows(None, wanted_countries(["europe"]))
    assert allows(None, wanted_countries(["budapest"]))


def test_a_user_who_filtered_nothing_gets_everything():
    assert allows("US", None)
    assert allows(None, None)


def test_end_to_end_the_case_that_was_described():
    """A user searching the EU. A UK posting is dropped; a German one is kept; one that names
    nowhere is kept."""
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
