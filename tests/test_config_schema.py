"""T9 — config validation."""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")
from pydantic import ValidationError

from libs.contracts.config import (
    CONFIG_SCHEMA_VERSION,
    MAX_KEYWORDS_PER_USER,
    MIN_GREEN_WORDS,
    Currency,
    Language,
    Location,
    Platform,
    SearchSection,
    Seniority,
    UserConfigSchema,
    Workplace,
    complete_search,
    migrate_config,
)
from services.harvester.query_set import HARVEST_MAX_QUERIES_PER_USER, build_query_set

# Which overrides belong to a search section and which to the account.
SECTION_FIELDS = {"platform", "keywords", "exclude_keywords", "locations", "workplaces",
                  "posted_within_hours"}


# The least a search takes: three green words, a country, a way of working (on by default), a language.
WORDS = ["python developer", "golang", "sql"]
LEAST = {"keywords": WORDS, "locations": ["germany"]}


def _valid(**overrides) -> UserConfigSchema:
    section = dict(LEAST)
    account = {"languages": ["en"]}
    for key, value in overrides.items():
        (section if key in SECTION_FIELDS else account)[key] = value
    return UserConfigSchema(sections=[section], **account)


def _field(config: UserConfigSchema, name: str):
    """Read a field from wherever it lives now - the account, or its first section."""
    return getattr(config, name) if hasattr(config, name) else getattr(config.sections[0], name)


def _keyword_cap() -> int:
    """The keyword ceiling, read off the field rather than off an exported JSON Schema."""
    return next(rule.max_length for rule in SearchSection.model_fields["keywords"].metadata
                if getattr(rule, "max_length", None) is not None)


# ── the minimum viable config ─────────────────────────────────────────────────────────────────

def test_three_words_a_country_and_a_language_are_enough():
    """The least a search takes (owner, 2026-11-04); the way of working starts switched on."""
    section = _valid().sections[0]
    assert section.keywords == WORDS
    assert section.platform == Platform.ALL, "one section, targeting everything, is the default"
    assert section.workplaces == [Workplace.REMOTE, Workplace.HYBRID], "as the question it replaced"
    assert section.posted_within_hours == 24, "24h steady state; cold start widens it once"


@pytest.mark.parametrize(("change", "said"), [
    ({"keywords": WORDS[:MIN_GREEN_WORDS - 1]}, f"add at least {MIN_GREEN_WORDS} green words"),
    ({"locations": []}, "pick at least one country"),
    ({"workplaces": []}, "switch on at least one way of working"),
    ({"languages": []}, "pick at least one language"),
])
def test_a_search_missing_any_part_is_refused_and_says_what_to_add(change, said):
    """Without a country no job comes in for it: jobs are fetched country by country."""
    with pytest.raises(ValidationError) as exc:
        _valid(**change)
    assert exc.value.errors()[0]["msg"] == said


def test_the_same_word_twice_is_one_word_towards_the_three():
    with pytest.raises(ValidationError, match="green words"):
        UserConfigSchema(languages=["en"], sections=[
            {"platform": "all", "keywords": ["golang", "sql"], "locations": ["germany"]},
            {"platform": "linkedin", "keywords": ["SQL"], "locations": ["germany"]}])


def test_a_stored_search_saved_before_the_minimums_reads_as_incomplete():
    assert complete_search({"sections": [{"keywords": WORDS, "locations": ["germany"]}], "languages": ["en"]})
    assert not complete_search({"sections": [{"keywords": WORDS}], "languages": ["en"]})
    assert not complete_search(None)


def test_a_config_with_no_keywords_is_rejected():
    """Zero keywords means zero matches, which is the failure this whole schema exists to prevent."""
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(sections=[{"keywords": []}])
    assert "keywords" in str(exc.value)


# ── bounds are rejections, never silent clamps ────────────────────────────────────────────────

def test_too_many_keywords_is_rejected_not_truncated():
    """Truncating would silently drop terms the user chose."""
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(sections=[
            {"keywords": [f"term{i}" for i in range(MAX_KEYWORDS_PER_USER + 1)]}])
    assert "keywords" in str(exc.value)


def test_the_cap_spans_sections_rather_than_applying_to_each():
    """Per-section caps would let five sections buy five times the fan-out budget - silently."""
    half = MAX_KEYWORDS_PER_USER // 2 + 1
    with pytest.raises(ValidationError, match="exceeds the limit"):
        UserConfigSchema(languages=["en"], sections=[
            {"platform": "all", "keywords": [f"a{i}" for i in range(half)], "locations": ["germany"]},
            {"platform": "linkedin", "keywords": [f"b{i}" for i in range(half)], "locations": ["germany"]}])


def test_one_platform_cannot_have_two_sections():
    """The fold would merge them, so the user would see terms they believed they had kept separate."""
    with pytest.raises(ValidationError, match="at most one section"):
        UserConfigSchema(languages=["en"], sections=[
            {"platform": "linkedin", "keywords": WORDS, "locations": ["germany"]},
            {"platform": "linkedin", "keywords": ["rust"], "locations": ["germany"]}])


@pytest.mark.parametrize("bad", ["a", " x ", "z" * 41])
def test_keyword_length_is_bounded_and_names_the_field(bad):
    """Under 2 characters matches everything; over 40 is not a search term."""
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(sections=[{"keywords": [bad]}])
    assert "keyword" in str(exc.value).lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [("min_salary", -1), ("min_salary", 500_001),
     ("posted_within_hours", 0), ("posted_within_hours", 169)])
def test_out_of_range_values_are_rejected_with_the_field_named(field, value):
    with pytest.raises(ValidationError) as exc:
        _valid(**{field: value})
    assert field in str(exc.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("min_salary", 0), ("min_salary", 500_000),
     # 1 hour, not 6.
     ("posted_within_hours", 1), ("posted_within_hours", 168)])
def test_the_boundaries_themselves_are_accepted(field, value):
    """Off-by-one on a bound is a real bug: it makes a documented-legal value mysteriously fail."""
    assert _field(_valid(**{field: value}), field) == value


def test_as_many_red_words_as_the_form_offers_are_accepted():
    """The form takes 25 of each; the 11th red word used to come back as an error."""
    words = [f"word{index}" for index in range(MAX_KEYWORDS_PER_USER)]
    assert len(_valid(exclude_keywords=words).sections[0].exclude_keywords) == 25
    with pytest.raises(ValidationError):
        _valid(exclude_keywords=[*words, "one more"])


def test_a_stored_config_with_the_retired_daily_cap_still_reads():
    """`max_notifications_per_day` had no reader and is gone; rows that carry it must not break."""
    stored = {"sections": [{"platform": "all", **LEAST}], "languages": ["en"],
              "max_notifications_per_day": 20}
    assert UserConfigSchema.model_validate(migrate_config(stored)).sections[0].keywords == WORDS


def test_too_many_locations_is_rejected():
    """Bound at `MAX_LOCATIONS_PER_USER` rather than a literal, because the number moved."""
    from libs.contracts.config import MAX_LOCATIONS_PER_USER

    assert len(list(Location)) > MAX_LOCATIONS_PER_USER, "otherwise this proves nothing"
    with pytest.raises(ValidationError):
        _valid(locations=list(Location)[:MAX_LOCATIONS_PER_USER + 1])


# ── controlled vocabularies ───────────────────────────────────────────────────────────────────

def test_locations_are_a_picklist_not_free_text():
    """Not only a UI decision."""
    with pytest.raises(ValidationError):
        _valid(locations=["Greater London Area, United Kingdom"])
    assert _valid(locations=["ireland"]).sections[0].locations == [Location.IRELAND]


def test_a_city_sent_by_a_page_loaded_before_cities_left_my_search_is_saved_as_its_country():
    """A tab opened before the deploy still holds "london" and would otherwise be refused on Save."""
    assert _valid(locations=["london", "united_kingdom", "berlin"]).sections[0].locations == [
        Location.UNITED_KINGDOM, Location.GERMANY]


@pytest.mark.parametrize(
    ("field", "bad"),
    [("seniority", ["overlord"]), ("languages", ["klingon"]),
     ("workplaces", ["whenever"]), ("salary_currency", "DOGE")])
def test_every_enum_field_refuses_values_outside_its_vocabulary(field, bad):
    with pytest.raises(ValidationError):
        _valid(**{field: bad})


def test_unknown_fields_are_refused():
    """`extra="forbid"` so a typo'd or stale field fails loudly instead of being silently ignored."""
    with pytest.raises(ValidationError):
        _valid(remote_ploicy="any")


# ── normalisation ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_keywords_collapse_case_insensitively():
    """Two spellings of one term become two entries in the query set and spend the budget twice."""
    config = _valid(keywords=["Python Developer", "python developer", "PYTHON DEVELOPER", "go", "sql"])
    assert config.sections[0].keywords == ["Python Developer", "go", "sql"]


def test_whitespace_is_collapsed_not_rejected():
    """Users paste. Being strict about incidental whitespace is hostility, not validation."""
    assert _valid(keywords=["  python   developer  ", "go", "sql"]).sections[0].keywords == \
        ["python developer", "go", "sql"]


def test_duplicate_enum_selections_collapse():
    config = _valid(locations=["united_kingdom", "united_kingdom", "germany"],
                    seniority=["senior", "senior"], languages=["en", "en"])
    assert config.sections[0].locations == [Location.UNITED_KINGDOM, Location.GERMANY]
    assert config.seniority == [Seniority.SENIOR]
    assert config.languages == [Language.EN]


# ── the contract the frontend is generated from ───────────────────────────────────────────────

def test_the_keyword_cap_is_the_one_the_harvester_enforces():
    """Two numbers, one question: how many search terms one account may have.

    This used to read an exported JSON Schema and also loop over every field asserting it appeared in
    that schema's `properties` — which pydantic guarantees, so those loops could not fail. The cap
    equality is the part that can, and it is read straight off the field now: the export existed to
    generate a form that is in fact hand-written TSX, and it is gone (2026-08-11).
    """
    assert _keyword_cap() == HARVEST_MAX_QUERIES_PER_USER, (
        "the config accepts a different number of keywords than the harvester will run")


def test_the_schema_version_is_recorded_so_old_rows_stay_readable():
    """`user_config.schema_version` is what lets a shape change be migrated rather than guessed."""
    assert isinstance(CONFIG_SCHEMA_VERSION, int) and CONFIG_SCHEMA_VERSION >= 1


# ── the seam: a validated config must actually drive the harvester ────────────────────────────

def test_a_validated_config_produces_the_expected_query_set():
    """Neither test above would notice if the schema and the harvester stopped agreeing."""
    import uuid

    user = uuid.uuid4()
    config = UserConfigSchema(languages=["en"], sections=[{
        "keywords": ["Python Developer", "golang", "sql"],
        "locations": ["united_kingdom", "germany"],
        "remote_policy": "remote_only",
        "posted_within_hours": 24}])

    query_set = build_query_set({user: config.model_dump()}, ["linkedin"])

    assert len(query_set) == 6, "3 keywords x 2 locations x 1 source"
    assert all(key.is_remote for key in query_set), "remote_only must reach the source"
    assert {k.location_norm for k in query_set} == {"united_kingdom", "germany"}
    assert all(users == frozenset({user}) for users in query_set.values())


def test_the_per_user_cap_is_one_definition_not_two():
    """This rule was briefly written twice — a literal in the schema and a constant in the harvester."""
    assert HARVEST_MAX_QUERIES_PER_USER is MAX_KEYWORDS_PER_USER
    assert _keyword_cap() == MAX_KEYWORDS_PER_USER

    with pytest.raises(ValidationError):
        UserConfigSchema(sections=[
            {"keywords": [f"term{i}" for i in range(MAX_KEYWORDS_PER_USER + 1)]}])


def test_currency_is_offered_as_a_picklist():
    assert _valid(min_salary=60000, salary_currency="GBP").salary_currency == Currency.GBP


# ── sections, and reading a config written before they existed ────────────────────────────────

def test_a_platform_section_only_reaches_that_platform():
    """The whole point of sections: terms a user wants on LinkedIn and nowhere else."""
    import uuid

    user = uuid.uuid4()
    config = UserConfigSchema(languages=["en"], sections=[
        {"platform": "all", "keywords": ["golang", "sql", "go"], "locations": ["germany"]},
        {"platform": "linkedin", "keywords": ["rust"], "locations": ["germany"]}])

    query_set = build_query_set({user: config.model_dump()}, ["indeed", "google", "linkedin"])
    by_term = {}
    for key in query_set:
        by_term.setdefault(key.keyword_norm, set()).add(key.source)

    assert by_term["golang"] == {"indeed", "google", "linkedin"}
    assert by_term["rust"] == {"linkedin"}


def test_a_v1_config_still_folds_exactly_as_it_did():
    """Stored rows are read through `migrate_config` rather than rewritten by a deploy."""
    import uuid

    user = uuid.uuid4()
    v1 = {"keywords": WORDS, "locations": ["london"], "languages": ["en"],
          "remote_policy": "remote_only", "posted_within_hours": 48}

    migrated = migrate_config(v1)
    assert migrated["sections"][0]["platform"] == Platform.ALL
    assert UserConfigSchema.model_validate(migrated).sections[0].posted_within_hours == 48

    query_set = build_query_set({user: v1}, ["indeed", "google"])
    assert len(query_set) == 6, "three keywords, one location, two sources - unchanged from v1"
    assert all(key.is_remote for key in query_set)


def test_migrating_a_config_twice_changes_nothing():
    """It runs on every read."""
    once = migrate_config({"keywords": ["go"], "locations": ["london"]})
    assert migrate_config(once) == once


