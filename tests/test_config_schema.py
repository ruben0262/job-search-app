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
    RemotePolicy,
    Seniority,
    UserConfigSchema,
    json_schema,
)
from services.harvester.query_set import HARVEST_MAX_QUERIES_PER_USER, build_query_set


def _valid(**overrides):
    base = {"keywords": ["python developer"]}
    base.update(overrides)
    return UserConfigSchema(**base)


# ── the minimum viable config ─────────────────────────────────────────────────────────────────

def test_a_single_keyword_is_enough():
    """Onboarding must not demand a fully-specified config before showing anything."""
    config = _valid()
    assert config.keywords == ["python developer"]
    assert config.remote_policy == RemotePolicy.REMOTE_OR_HYBRID
    assert config.posted_within_hours == 72


def test_a_config_with_no_keywords_is_rejected():
    """Zero keywords means zero matches, which is the failure this whole schema exists to prevent."""
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(keywords=[])
    assert "keywords" in str(exc.value)


# ── bounds are rejections, never silent clamps ────────────────────────────────────────────────

def test_too_many_keywords_is_rejected_not_truncated():
    """Truncating would silently drop terms the user chose, leaving them wondering why a search
    they configured never returns anything.

    Derived from the constant, not a literal. An earlier version hardcoded 6 against a cap of 5, so
    raising the cap to 25 turned a real assertion into one that passed by accident.
    """
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(keywords=[f"term{i}" for i in range(MAX_KEYWORDS_PER_USER + 1)])
    assert "keywords" in str(exc.value)


@pytest.mark.parametrize("bad", ["a", " x ", "z" * 41])
def test_keyword_length_is_bounded_and_names_the_field(bad):
    """Under 2 characters matches everything; over 40 is not a search term."""
    with pytest.raises(ValidationError) as exc:
        UserConfigSchema(keywords=[bad])
    assert "keyword" in str(exc.value).lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [("min_salary", -1), ("min_salary", 500_001),
     ("max_notifications_per_day", 0), ("max_notifications_per_day", 101),
     ("posted_within_hours", 5), ("posted_within_hours", 169)])
def test_out_of_range_values_are_rejected_with_the_field_named(field, value):
    with pytest.raises(ValidationError) as exc:
        _valid(**{field: value})
    assert field in str(exc.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("min_salary", 0), ("min_salary", 500_000),
     ("max_notifications_per_day", 1), ("max_notifications_per_day", 100),
     ("posted_within_hours", 6), ("posted_within_hours", 168)])
def test_the_boundaries_themselves_are_accepted(field, value):
    """Off-by-one on a bound is a real bug: it makes a documented-legal value mysteriously fail."""
    assert getattr(_valid(**{field: value}), field) == value


def test_too_many_locations_is_rejected():
    with pytest.raises(ValidationError):
        _valid(locations=list(Location)[:6])


# ── controlled vocabularies ───────────────────────────────────────────────────────────────────

def test_locations_are_a_picklist_not_free_text():
    """Not only a UI decision. Harvest dedup folds on a normalised location, so free text would
    multiply the query set with spellings of the same place and spend the fan-out budget on
    duplicates (`HARVESTER_PLAN.md` §2)."""
    with pytest.raises(ValidationError):
        _valid(locations=["Greater London Area, United Kingdom"])
    assert _valid(locations=["london"]).locations == [Location.LONDON]


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
    assert config.keywords == ["Python Developer"]


def test_whitespace_is_collapsed_not_rejected():
    """Users paste. Being strict about incidental whitespace is hostility, not validation."""
    assert _valid(keywords=["  python   developer  "]).keywords == ["python developer"]


def test_duplicate_enum_selections_collapse():
    config = _valid(locations=["london", "london", "berlin"],
                    seniority=["senior", "senior"], languages=["en", "en"])
    assert config.locations == [Location.LONDON, Location.BERLIN]
    assert config.seniority == [Seniority.SENIOR]
    assert config.languages == [Language.EN]


# ── the contract the frontend is generated from ───────────────────────────────────────────────

def test_the_json_schema_exposes_every_field_and_its_bounds():
    """The form and the TypeScript types are generated from this, so a field missing here is a
    field the user can never set (`CODE_STRUCTURE.md` §8)."""
    schema = json_schema()
    for field in UserConfigSchema.model_fields:
        assert field in schema["properties"], f"{field} would be invisible to the form"
    assert schema["properties"]["keywords"]["maxItems"] == HARVEST_MAX_QUERIES_PER_USER


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
    config = UserConfigSchema(
        keywords=["Python Developer", "golang"],
        locations=["london", "berlin"],
        remote_policy="remote_only",
        posted_within_hours=24)

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
    assert json_schema()["properties"]["keywords"]["maxItems"] == MAX_KEYWORDS_PER_USER

    with pytest.raises(ValidationError):
        UserConfigSchema(keywords=[f"term{i}" for i in range(MAX_KEYWORDS_PER_USER + 1)])


def test_currency_is_offered_as_a_picklist():
    assert _valid(min_salary=60000, salary_currency="GBP").salary_currency == Currency.GBP
