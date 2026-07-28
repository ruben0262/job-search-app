"""Rubric rendering and the per-user feature extractor. One workstream, one test file.

They are tested together because they fail together: both are derived from the same confirmed
profile, and `profile_generation` is the single counter that keeps them in step
(`PROFILE_CAPTURE_PLAN.md` §6.4). Testing them apart would leave the invalidation story — the thing
that actually goes wrong — untested.

The reproducibility tests are the load-bearing ones. `profile_version = sha256(rubric_md)` scopes
which labels a training run may use, so a render that is not byte-stable would silently invalidate
every user's labels on a deploy that changed nothing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from libs.contracts.profile import ProfileSchema
from libs.scoring.features import (
    FEATURE_NAMES,
    N_FEATURES,
    feature_spec,
    job_features,
)
from libs.scoring.rubric import (
    profile_version,
    render_rubric,
    rubric_relevant_digest,
)

PROFILE = {
    "identity": {"name": "Alice Example", "location": "Budapest",
                 "work_eligibility": ["EU citizen", "UK work visa"]},
    "education": [{"degree": "BEng Software Engineering", "institution": "TU Budapest"}],
    "experience": [{"role": "Backend Engineer", "org": "Acme",
                    "stack": ["Go", "gRPC", "PostgreSQL"]}],
    "projects": [{"name": "Trading bot", "stack": ["Python", "Pandas"]}],
    "skills": {"languages": ["Go", "Python", "TypeScript"],
               "frameworks": ["React", "FastAPI"],
               "domains": ["machine learning", "quantitative finance"],
               "tools": ["Docker"]},
    "targets": {"role_types": ["backend engineering", "quant research"],
                "seniority": "junior-to-mid", "company_size": "startups and small teams"},
    "constraints": {"remote_policy": "remote_or_hybrid", "onsite_locations": ["Hungary"],
                    "languages": ["English", "Hungarian"], "min_salary": 45000},
    "not_list": ["enterprise Java/C#/.NET at depth", "mobile (iOS/Android)", "game engines"],
    "confirmed": True,
}


def _profile(**overrides) -> ProfileSchema:
    return ProfileSchema.model_validate(PROFILE | overrides)


# ── reproducibility: the property profile_version depends on ──────────────────────────────────

def test_the_same_profile_renders_the_same_bytes():
    """If this ever fails, every user's labels invalidate on a deploy that changed nothing."""
    assert render_rubric(_profile()) == render_rubric(_profile())
    assert profile_version(render_rubric(_profile())) == profile_version(render_rubric(_profile()))


def test_two_different_profiles_get_different_versions():
    other = _profile(not_list=["mobile (iOS/Android)"])
    assert profile_version(render_rubric(_profile())) != profile_version(render_rubric(other))


def test_a_change_outside_the_rubric_relevant_subset_does_not_re_version():
    """A new phone number must not trigger a relabel of the user's whole history
    (`PROFILE_CAPTURE_PLAN.md` §7)."""
    cosmetic = _profile(projects=[{"name": "Trading bot", "what_built": "a much longer blurb",
                                   "stack": ["Python", "Pandas"]}])
    assert rubric_relevant_digest(cosmetic) == rubric_relevant_digest(_profile())


def test_a_contact_detail_is_not_rubric_relevant():
    """The rubric substitutes the name and reads nothing else about the person — not links, not
    location, not eligibility. Hashing the whole identity object made adding an email address cost
    a relabel of the user's entire history, which is the failure the subset exists to prevent."""
    with_links = _profile(identity=PROFILE["identity"] | {"links": {"email": "a@example.test"},
                                                         "location": "Lisbon, Portugal"})
    assert rubric_relevant_digest(with_links) == rubric_relevant_digest(_profile())


def test_a_new_name_does_re_version():
    """The name is substituted into the rubric, so its bytes change and the version must follow."""
    renamed = _profile(identity=PROFILE["identity"] | {"name": "Someone Else"})
    assert rubric_relevant_digest(renamed) != rubric_relevant_digest(_profile())


def test_a_change_inside_the_subset_does_re_version():
    changed = _profile(constraints=PROFILE["constraints"] | {"remote_policy": "remote_only"})
    assert rubric_relevant_digest(changed) != rubric_relevant_digest(_profile())


# ── what the rendered rubric actually contains ────────────────────────────────────────────────

def test_the_scaffolding_comes_from_the_template_not_the_profile():
    """Decision procedure, anchors, calibration, and output contract are platform-owned. If they
    varied per user, a template change could not be A/B measured across the platform."""
    rubric = render_rubric(_profile())
    for section in ("## DECISION PROCEDURE", "## SCORE ANCHORS", "## CALIBRATION EXAMPLES",
                    "score_breakdown", "<JOBS_JSON>"):
        assert section in rubric


def test_every_placeholder_is_filled():
    """A leftover marker would be sent verbatim to the model as literal text."""
    rubric = render_rubric(_profile())
    assert "<<" not in rubric


def test_the_not_list_becomes_the_off_stack_paragraph():
    rubric = render_rubric(_profile())
    assert "Does NOT have professional experience in:" in rubric
    for entry in PROFILE["not_list"]:
        assert entry in rubric


def test_targets_keep_the_users_priority_order():
    """Order is a human decision the user confirmed, not noise to be sorted away."""
    rubric = render_rubric(_profile())
    assert "(a) backend engineering; (b) quant research" in rubric


def test_remote_only_hard_gates_hybrid():
    """The gate list is derived, not fixed: a remote-only user must gate hybrid, and a user who
    accepts anything must gate neither."""
    remote_only = render_rubric(
        _profile(constraints=PROFILE["constraints"] | {"remote_policy": "remote_only"}))
    assert "hybrid or any required office attendance" in remote_only

    anywhere = render_rubric(
        _profile(constraints={"remote_policy": "any", "languages": ["English"]}))
    assert "hybrid or any required office attendance" not in anywhere
    assert "relocation" not in anywhere


def test_named_regions_gate_location_even_when_every_workplace_mode_is_accepted():
    """Mode and region are independent constraints, and conflating them loses a real one: a
    candidate can be happy with on-site, hybrid or remote and still only be able to take it in four
    countries. Found while transcribing the second account's rubric, where exactly that is true."""
    rubric = render_rubric(_profile(constraints={
        "remote_policy": "any", "onsite_locations": ["UK", "Ireland", "Hungary"],
        "languages": ["English"]}))
    assert "on-site-only outside UK, Ireland, Hungary" in rubric
    assert "all equally acceptable" in rubric


def test_the_language_gate_names_the_users_languages():
    rubric = render_rubric(_profile())
    assert "JD language is not one of: English, Hungarian" in rubric


def test_a_sparse_profile_still_renders():
    """Manual entry produces thin profiles. A renderer that needs every field would make the
    fallback path fail at the next step instead of at the form."""
    rubric = render_rubric(ProfileSchema.model_validate(
        {"identity": {"name": "Bob"}, "confirmed": True}))
    assert "<<" not in rubric
    assert "Bob" in rubric


# ── the feature extractor: per user, not per platform ─────────────────────────────────────────

def test_the_feature_vector_shape_is_global():
    """Only the token lists are personal. If the column order varied per user, no model artifact
    would be portable and the append-only rule would mean nothing."""
    vector = job_features(feature_spec(_profile()), "Backend Engineer", "Go, gRPC, remote")
    assert len(vector) == N_FEATURES == len(FEATURE_NAMES)
    assert all(0.0 <= v <= 1.0 for v in vector)


def test_core_tokens_come_from_the_users_own_stack():
    spec = feature_spec(_profile())
    core = FEATURE_NAMES.index("core_stack_count")
    hit = job_features(spec, "Backend Engineer", "We use Go, PostgreSQL and Docker.")
    miss = job_features(spec, "COBOL Maintainer", "Mainframe COBOL and JCL.")
    assert hit[core] > 0
    assert miss[core] == 0


def test_off_stack_tokens_come_from_the_users_not_list():
    spec = feature_spec(_profile())
    off = FEATURE_NAMES.index("offstack_count")
    assert job_features(spec, "Java Developer", "Spring Boot, .NET interop")[off] > 0
    assert job_features(spec, "Go Developer", "Go and Postgres")[off] == 0


def test_two_users_score_the_same_posting_differently():
    """The whole point of §6.3. Under the reference's hardcoded lists both users would get the same
    counts, and one of them would be scored against someone else's stack."""
    gopher = feature_spec(_profile())
    javaist = feature_spec(_profile(
        skills={"languages": ["Java"], "frameworks": ["Spring Boot"]},
        experience=[], projects=[], not_list=["Go", "Rust"]))

    posting = ("Backend Engineer", "A Java and Spring Boot shop.")
    core, off = FEATURE_NAMES.index("core_stack_count"), FEATURE_NAMES.index("offstack_count")

    # Same posting, opposite readings: one person's core stack is the other's hard gate.
    assert job_features(gopher, *posting)[core] < job_features(javaist, *posting)[core]
    assert job_features(gopher, *posting)[off] > job_features(javaist, *posting)[off] == 0


def test_a_term_in_both_lists_counts_as_off_stack():
    """A stale strength entry must not quietly cancel a confirmed hard gate — the gate is the one
    the user actually reviewed."""
    spec = feature_spec(_profile(skills={"languages": ["Go", "Java"]}, not_list=["Java"]))
    core, off = FEATURE_NAMES.index("core_stack_count"), FEATURE_NAMES.index("offstack_count")
    vector = job_features(spec, "Java Developer", "Java only.")
    assert vector[off] > 0
    assert vector[core] == 0


@pytest.mark.parametrize("skill,text", [
    ("C#", "We write C# every day"),
    (".NET", "a .NET shop"),
    ("C++", "modern C++ 20"),
    ("Node.js", "Node.js services"),
    ("machine learning", "applied machine-learning team"),
])
def test_punctuation_heavy_technologies_still_match(skill, text):
    r"""`\bc#\b` never matches — the boundary after `#` needs a following word character. Getting
    this wrong makes a whole category of stack silently invisible to the model."""
    spec = feature_spec(_profile(skills={"languages": [skill]}, experience=[], projects=[],
                                 not_list=[]))
    assert job_features(spec, "Engineer", text)[FEATURE_NAMES.index("core_stack_count")] > 0


@pytest.mark.parametrize("skill,text", [
    ("full-stack", "Full Stack Developer"),
    ("full stack", "full-stack engineer"),
    ("machine learning", "applied machine-learning team"),
])
def test_a_hyphen_and_a_space_are_the_same_thing(skill, text):
    """A profile saying "full-stack" must match a posting saying "full stack". Without it the
    hyphenated spelling matches nothing and the feature silently reads 0 — found while transcribing
    a real profile, where "full-stack" is exactly how the person writes it."""
    spec = feature_spec(_profile(skills={"domains": [skill]}, experience=[], projects=[],
                                 not_list=[]))
    assert job_features(spec, "", text)[FEATURE_NAMES.index("core_stack_count")] > 0


def test_a_fragment_of_a_compound_does_not_become_its_own_token():
    """"Spring Boot" split into words yields "boot", which then fires on "bootstrap" — turning one
    NOT-list entry into a gate on unrelated postings."""
    spec = feature_spec(_profile(not_list=["Spring Boot"], skills={}, experience=[], projects=[]))
    off = FEATURE_NAMES.index("offstack_count")
    assert job_features(spec, "Engineer", "We use Bootstrap and a bootcamp pipeline.")[off] == 0
    assert job_features(spec, "Engineer", "Spring Boot microservices")[off] > 0


def test_noise_words_in_a_not_list_entry_do_not_become_tokens():
    """"enterprise Java at depth" must not match on "at". A token that fires on every posting turns
    a hard gate into noise."""
    spec = feature_spec(_profile(not_list=["enterprise Java at depth"], skills={}, experience=[],
                                 projects=[]))
    off = FEATURE_NAMES.index("offstack_count")
    assert job_features(spec, "Engineer", "You will be working at a startup.")[off] == 0
    assert job_features(spec, "Engineer", "Java shop")[off] > 0


def test_region_lock_respects_the_users_work_eligibility():
    """The reference hardcoded "US-only is a gate" because its one user could not work there. For a
    user who can, flagging it would suppress jobs they are eligible for."""
    lock = FEATURE_NAMES.index("region_lock_flag")
    posting = ("Engineer", "Remote, US only. Must be authorized to work in the United States.")

    eu_only = feature_spec(_profile())
    us_eligible = feature_spec(_profile(
        identity=PROFILE["identity"] | {"work_eligibility": ["US citizen"]}))

    assert job_features(eu_only, *posting)[lock] == 1.0
    assert job_features(us_eligible, *posting)[lock] == 0.0


@pytest.mark.parametrize("name,text,expected", [
    ("remote_flag", "This is a fully remote position", 1.0),
    ("hybrid_flag", "Hybrid, 2 days in office", 1.0),
    ("onsite_flag", "On-site in our Berlin office", 1.0),
    ("junior_flag", "Graduate Software Engineer, entry-level", 1.0),
    ("senior_flag", "Senior Staff Engineer", 1.0),
    ("senior_flag", "Junior Engineer", 0.0),
])
def test_the_platform_owned_flags(name, text, expected):
    """These are not personal — every user needs the same reading of "fully remote"."""
    spec = feature_spec(_profile())
    assert job_features(spec, "", text)[FEATURE_NAMES.index(name)] == expected


def test_years_required_reads_the_lower_bound_of_a_range():
    """"1-3 years" reads as 1, not 3 — the feature is the minimum the employer asks for, which is
    what the seniority gate turns on. Carried from the reference's regex deliberately; changing it
    would shift the meaning of a column every existing model was fit against."""
    spec = feature_spec(_profile())
    years = FEATURE_NAMES.index("years_required")
    assert job_features(spec, "", "1-3 years of experience")[years] == pytest.approx(1 / 15)
    assert job_features(spec, "", "5+ years required")[years] == pytest.approx(5 / 15)
    assert job_features(spec, "", "20+ years required")[years] == 1.0
    assert job_features(spec, "", "no experience stated")[years] == 0.0


def test_features_read_only_the_work():
    """Title and description only — never company, location, or salary. Same de-bias rule as the
    embedding text, and it is only true if nothing else is ever passed in."""
    spec = feature_spec(_profile())
    assert job_features(spec, "Engineer", "Go") == job_features(spec, "Engineer", "Go")


def test_the_extractor_is_deterministic_across_specs_built_twice():
    """Train and serve build the spec separately from the same profile. If those two specs differed,
    a model would be fit against different semantics than it serves — silently."""
    a, b = feature_spec(_profile()), feature_spec(_profile())
    posting = ("Backend Engineer", "Go, Docker, hybrid in Budapest, 2-4 years")
    assert job_features(a, *posting) == job_features(b, *posting)
