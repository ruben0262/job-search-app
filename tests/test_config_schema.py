"""T9 — config validation. The primary onboarding failure mode.

`TENANT_SAFETY.md` §4's requirement is that a non-technical user **cannot** produce a config that is
broken or unrealistic. The way to get that is bounded types and controlled vocabularies, not free
text plus a wall of error messages — so most of these tests assert that something is *impossible*,
not that an error message is pretty.

Two rules carry the weight:

- **Nothing is silently clamped.** An out-of-range value is a rejection naming the field. Silent
  truncation is how a user concludes the product is broken rather than that they hit a limit.
- **The server is the authority.** These same bounds exist in the database; the form is the
  usability layer. A write path that bypassed the form must not bypass the bounds.

The last section tests the seam nobody would notice breaking: a *validated* config has to actually
drive the harvester. Testing the schema and the query-set builder separately would leave the join
between them unproven, and that join is where a rename or an enum change would silently stop
producing queries.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")
from pydantic import ValidationError

from libs.contracts.config import (
    CONFIG_SCHEMA_VERSION,
    MAX_KEYWORDS_PER_USER,
    Currency,
    Language,
    Location,
    Platform,
    RemotePolicy,
    SearchSection,
    Seniority,
    UserConfigSchema,
    json_schema,
    migrate_config,
)
from services.harvester.query_set import HARVEST_MAX_QUERIES_PER_USER, build_query_set

# Which overrides belong to a search section and which to the account. The helper routes them so
# each test keeps saying what it means - `_valid(min_salary=...)` and `_valid(locations=...)`
# read as they did under the flat schema, and the tests stay about behaviour rather than shape.
SECTION_FIELDS = {"platform", "keywords", "exclude_keywords", "locations", "remote_policy",
                  "posted_within_hours"}


def _valid(**overrides) -> UserConfigSchema:
    section = {"keywords": ["python developer"]}
    account = {}
    for key, value in overrides.items():
        (section if key in SECTION_FIELDS else account)[key] = value
    return UserConfigSchema(sections=[section], **account)


def _field(config: UserConfigSchema, name: str):
    """Read a field from wherever it lives now - the account, or its first section."""
    return getattr(config, name) if hasattr(config, name) else getattr(config.sections[0], name)


def _section_schema() -> dict:
    """`SearchSection` properties, which pydantic exports under `$defs`."""
    return json_schema()["$defs"]["SearchSection"]["properties"]


# ── the minimum viable config ─────────────────────────────────────────────────────────────────

def test_a_single_keyword_is_enough():
    """Onboarding must not demand a fully-specified config before showing anything."""
    section = _valid().sections[0]
    assert section.keywords == ["python developer"]
    assert section.platform == Platform.ALL, "one section, targeting everything, is the default"
    assert section.remote_policy == RemotePolicy.REMOTE_OR_HYBRID
    assert section.posted_within_hours == 24, "24h steady state; cold start widens it once"


def test_a_config_with_no_keywords_is_rejected():
    """Zero keywords means zero matches, which is the failure this whole schema exists to prevent."""
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(sections=[{"keywords": []}])
    assert "keywords" in str(exc.value)


# ── bounds are rejections, never silent clamps ────────────────────────────────────────────────

def test_too_many_keywords_is_rejected_not_truncated():
    """Truncating would silently drop terms the user chose, leaving them wondering why a search
    they configured never returns anything.

    Derived from the constant, not a literal. An earlier version hardcoded 6 against a cap of 5, so
    raising the cap to 25 turned a real assertion into one that passed by accident.
    """
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(sections=[
            {"keywords": [f"term{i}" for i in range(MAX_KEYWORDS_PER_USER + 1)]}])
    assert "keywords" in str(exc.value)


def test_the_cap_spans_sections_rather_than_applying_to_each():
    """Per-section caps would let five sections buy five times the fan-out budget - silently,
    since each section on its own would look compliant."""
    half = MAX_KEYWORDS_PER_USER // 2 + 1
    with pytest.raises(ValidationError, match="exceeds the limit"):
        UserConfigSchema(sections=[
            {"platform": "all", "keywords": [f"a{i}" for i in range(half)]},
            {"platform": "linkedin", "keywords": [f"b{i}" for i in range(half)]}])


def test_one_platform_cannot_have_two_sections():
    """The fold would merge them, so the user would see terms they believed they had kept
    separate."""
    with pytest.raises(ValidationError, match="at most one section"):
        UserConfigSchema(sections=[{"platform": "linkedin", "keywords": ["go"]},
                                   {"platform": "linkedin", "keywords": ["rust"]}])


@pytest.mark.parametrize("bad", ["a", " x ", "z" * 41])
def test_keyword_length_is_bounded_and_names_the_field(bad):
    """Under 2 characters matches everything; over 40 is not a search term."""
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(sections=[{"keywords": [bad]}])
    assert "keyword" in str(exc.value).lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [("min_salary", -1), ("min_salary", 500_001),
     ("max_notifications_per_day", 0), ("max_notifications_per_day", 101),
     ("posted_within_hours", 0), ("posted_within_hours", 169)])
def test_out_of_range_values_are_rejected_with_the_field_named(field, value):
    with pytest.raises(ValidationError) as exc:
        _valid(**{field: value})
    assert field in str(exc.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("min_salary", 0), ("min_salary", 500_000),
     ("max_notifications_per_day", 1), ("max_notifications_per_day", 100),
     # 1 hour, not 6. The reference's frequent loop runs at two, and being first to a posting is
     # the single biggest thing a candidate controls — a six-hour floor made the platform's
     # tightest setting three times looser than the instance it was ported from.
     ("posted_within_hours", 1), ("posted_within_hours", 168)])
def test_the_boundaries_themselves_are_accepted(field, value):
    """Off-by-one on a bound is a real bug: it makes a documented-legal value mysteriously fail."""
    assert _field(_valid(**{field: value}), field) == value


def test_too_many_locations_is_rejected():
    """Bound at `MAX_LOCATIONS_PER_USER` rather than a literal, because the number moved: five was
    fewer than an honest answer to the form's own question, and the picker offers about twenty
    choices. The cap still exists -- queries are keywords x locations x sources -- so this asserts
    that one more than the limit is refused, whatever the limit currently is."""
    from libs.contracts.config import MAX_LOCATIONS_PER_USER

    assert len(list(Location)) > MAX_LOCATIONS_PER_USER, "otherwise this proves nothing"
    with pytest.raises(ValidationError):
        _valid(locations=list(Location)[:MAX_LOCATIONS_PER_USER + 1])


# ── controlled vocabularies ───────────────────────────────────────────────────────────────────

def test_locations_are_a_picklist_not_free_text():
    """Not only a UI decision. Harvest dedup folds on a normalised location, so free text would
    multiply the query set with spellings of the same place and spend the fan-out budget on
    duplicates (`HARVESTER_PLAN.md` §2)."""
    with pytest.raises(ValidationError):
        _valid(locations=["Greater London Area, United Kingdom"])
    assert _valid(locations=["london"]).sections[0].locations == [Location.LONDON]


@pytest.mark.parametrize(
    ("field", "bad"),
    [("seniority", ["overlord"]), ("languages", ["klingon"]),
     ("remote_policy", "whenever"), ("salary_currency", "DOGE")])
def test_every_enum_field_refuses_values_outside_its_vocabulary(field, bad):
    with pytest.raises(ValidationError):
        _valid(**{field: bad})


def test_unknown_fields_are_refused():
    """`extra="forbid"` so a typo'd or stale field fails loudly instead of being silently ignored —
    which would otherwise present as "I set that and nothing happened"."""
    with pytest.raises(ValidationError):
        _valid(remote_ploicy="any")


# ── normalisation ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_keywords_collapse_case_insensitively():
    """Two spellings of one term become two entries in the query set and spend the budget twice."""
    config = _valid(keywords=["Python Developer", "python developer", "PYTHON DEVELOPER"])
    assert config.sections[0].keywords == ["Python Developer"]


def test_whitespace_is_collapsed_not_rejected():
    """Users paste. Being strict about incidental whitespace is hostility, not validation."""
    assert _valid(keywords=["  python   developer  "]).sections[0].keywords == \
        ["python developer"]


def test_duplicate_enum_selections_collapse():
    config = _valid(locations=["london", "london", "berlin"],
                    seniority=["senior", "senior"], languages=["en", "en"])
    assert config.sections[0].locations == [Location.LONDON, Location.BERLIN]
    assert config.seniority == [Seniority.SENIOR]
    assert config.languages == [Language.EN]


# ── the contract the frontend is generated from ───────────────────────────────────────────────

def test_the_json_schema_exposes_every_field_and_its_bounds():
    """The form and the TypeScript types are generated from this, so a field missing here is a
    field the user can never set (`CODE_STRUCTURE.md` §8)."""
    schema = json_schema()
    for field in UserConfigSchema.model_fields:
        assert field in schema["properties"], f"{field} would be invisible to the form"
    for field in SearchSection.model_fields:
        assert field in _section_schema(), f"{field} would be invisible on the section form"
    assert _section_schema()["keywords"]["maxItems"] == HARVEST_MAX_QUERIES_PER_USER


def test_the_schema_version_is_recorded_so_old_rows_stay_readable():
    """`user_config.schema_version` is what lets a shape change be migrated rather than guessed."""
    assert isinstance(CONFIG_SCHEMA_VERSION, int) and CONFIG_SCHEMA_VERSION >= 1


# ── the seam: a validated config must actually drive the harvester ────────────────────────────

def test_a_validated_config_produces_the_expected_query_set():
    """Neither test above would notice if the schema and the harvester stopped agreeing.

    This is the join where a renamed field or a changed enum value would silently produce **zero**
    queries — a user configured, saved, and simply never harvested for.
    """
    import uuid

    user = uuid.uuid4()
    config = UserConfigSchema(sections=[{
        "keywords": ["Python Developer", "golang"],
        "locations": ["london", "berlin"],
        "remote_policy": "remote_only",
        "posted_within_hours": 24}])

    query_set = build_query_set({user: config.model_dump()}, ["linkedin"])

    assert len(query_set) == 4, "2 keywords x 2 locations x 1 source"
    assert all(key.is_remote for key in query_set), "remote_only must reach the source"
    assert {k.location_norm for k in query_set} == {"london", "berlin"}
    assert all(users == frozenset({user}) for users in query_set.values())


def test_the_per_user_cap_is_one_definition_not_two():
    """This rule was briefly written twice — a literal in the schema and a constant in the harvester.

    That is exactly how limits drift: raise one and the other silently accepts terms it will never
    serve. The fix was to move the cap into `libs`, the only package both may import from, rather
    than to write a test asserting two numbers happen to agree. This asserts the single definition
    is genuinely the one in force on both sides, and that it actually binds.
    """
    assert HARVEST_MAX_QUERIES_PER_USER is MAX_KEYWORDS_PER_USER
    assert _section_schema()["keywords"]["maxItems"] == MAX_KEYWORDS_PER_USER

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
    config = UserConfigSchema(sections=[
        {"platform": "all", "keywords": ["golang"]},
        {"platform": "linkedin", "keywords": ["rust"]}])

    query_set = build_query_set({user: config.model_dump()}, ["indeed", "google", "linkedin"])
    by_term = {}
    for key in query_set:
        by_term.setdefault(key.keyword_norm, set()).add(key.source)

    assert by_term["golang"] == {"indeed", "google", "linkedin"}
    assert by_term["rust"] == {"linkedin"}


def test_a_v1_config_still_folds_exactly_as_it_did():
    """Stored rows are read through `migrate_config` rather than rewritten by a deploy. A user's
    config must not change because the platform shipped."""
    import uuid

    user = uuid.uuid4()
    v1 = {"keywords": ["python developer"], "locations": ["london"],
          "remote_policy": "remote_only", "posted_within_hours": 48}

    migrated = migrate_config(v1)
    assert migrated["sections"][0]["platform"] == Platform.ALL
    assert UserConfigSchema.model_validate(migrated).sections[0].posted_within_hours == 48

    query_set = build_query_set({user: v1}, ["indeed", "google"])
    assert len(query_set) == 2, "one keyword, one location, two sources - unchanged from v1"
    assert all(key.is_remote for key in query_set)


def test_migrating_a_config_twice_changes_nothing():
    """It runs on every read. Wrapping an already-sectioned config would nest it and lose the
    keywords entirely."""
    once = migrate_config({"keywords": ["go"], "locations": ["london"]})
    assert migrate_config(once) == once
