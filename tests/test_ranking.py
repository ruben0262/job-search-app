"""Opportunity ranking and the eligibility filters."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from libs.tenant.filters import UserFilters, applicant_glut

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


@dataclass
class Item:
    """A corpus row, as the filters read it."""

    job_id: int = 1
    title: str = "Backend Engineer"
    company: str | None = "Acme"
    score: float = 80.0
    source: str | None = "linkedin"
    location: str | None = "Berlin, Germany"
    is_remote: bool | None = None
    description: str | None = ("We are hiring a backend engineer to work on our payments platform. "
                               "You will build services in Python and Go, own them in production, "
                               "and work with a small team of experienced engineers.")
    # Relative to the module's fixed NOW, and every call must pass `now=NOW`.
    posted_at: datetime | None = NOW - timedelta(hours=1)
    applicants: int | None = 5
    easy_apply: bool | None = None
    reposted: bool | None = None
    workplace: str | None = None
    # Detected once at describe time and stored on the row, like `country` and `workplace`.
    language: str | None = "en"


def filters(*, applicant_cap: int | None = 200, max_age_hours: int = 24,
            **overrides) -> UserFilters:
    """The same account, as the *pre-score* filter sees it."""
    return UserFilters.from_config({"languages": ["en"], **overrides},
                                   max_age_hours=max_age_hours, applicant_cap=applicant_cap)


def drops(item, rules_=None) -> str | None:
    """Why this account would never receive this posting, or `None`."""
    return (rules_ or filters()).unwanted(dataclasses.asdict(item))


# ── the buckets ───────────────────────────────────────────────────────────────────────────────

# ── the score ─────────────────────────────────────────────────────────────────────────────────

# ── ordering, where score and opportunity disagree ────────────────────────────────────────────

# ── the filters, and what they refuse to drop ─────────────────────────────────────────────────

def test_a_job_in_a_language_the_user_does_not_write_is_dropped():
    assert drops(Item(language="de")) == "language"


def test_the_filter_reads_the_stored_code_and_never_runs_a_detector():
    """Detection is a property of the posting: once at describe time, not per reader per pass."""
    import inspect

    from libs.tenant import filters as module

    body = inspect.getsource(module.UserFilters.unwanted)
    assert "detect_language" not in body, (
        "the same posting was detected again for every account and again on every retrain")
    # A German description with the stored code saying English is judged by the column.
    german_text = Item(language="en", description="Wir suchen einen Softwareentwickler. " * 5)
    assert drops(german_text) is None


def test_a_posting_whose_language_could_not_be_read_is_kept():
    """Fails open. Unknown is not evidence of anything."""
    assert drops(Item(language=None)) is None


def test_an_account_that_never_stated_a_language_keeps_everything():
    """Otherwise a user who skipped one onboarding question would have their entire feed silently emptied."""
    assert drops(Item(language="de"), UserFilters.from_config({}, max_age_hours=24)) is None


def switched(*ways: str) -> UserFilters:
    return filters(sections=[{"keywords": ["backend engineer"], "workplaces": list(ways)}])


def test_remote_and_hybrid_drop_an_explicit_onsite_job_wherever_it_is():
    """Was: on-site allowed only in Hungary, the reference owner's default, for every account."""
    for place in ("Budapest, Hungary", "Dublin, Ireland"):
        assert drops(Item(workplace="onsite", location=place), switched("remote", "hybrid")) == "workplace"


def test_every_switch_on_keeps_an_onsite_job_anywhere_and_place_is_the_country_filter_s_job():
    assert drops(Item(workplace="onsite", location="Dublin, Ireland"),
                 switched("remote", "hybrid", "onsite")) is None


def test_remote_alone_drops_hybrid_but_never_a_job_whose_workplace_is_unknown():
    """The filter only ever drops an *explicit* signal. Everything else fails open."""
    assert drops(Item(workplace="hybrid"), switched("remote")) == "workplace"
    assert drops(Item(workplace=None), switched("remote")) is None


def test_two_switches_on_keep_a_job_that_is_either_and_drop_the_third():
    """Switches are an "or": hybrid and on-site on is a job that is hybrid or on site, never remote."""
    rules = switched("hybrid", "onsite")
    kept = {way for way in ("remote", "hybrid", "onsite") if drops(Item(workplace=way), rules) is None}
    assert kept == {"hybrid", "onsite"}


def test_no_switch_on_narrows_nothing():
    """Like an empty country or language list: nothing picked is nothing ruled out."""
    assert all(drops(Item(workplace=way), switched()) is None for way in ("remote", "hybrid", "onsite"))


def test_a_search_saved_before_the_switches_admits_the_same_jobs():
    """Its stored answer is read as the switches that admit what it did, and no answer was remote or
    hybrid. Nobody's feed changes on the day the question did."""
    def kept(section: dict) -> set[str]:
        rules = filters(sections=[{"keywords": ["backend engineer"], **section}])
        return {way for way in ("remote", "hybrid", "onsite") if drops(Item(workplace=way), rules) is None}

    assert kept({"remote_policy": "remote_only"}) == {"remote"}
    assert kept({"remote_policy": "remote_or_hybrid"}) == {"remote", "hybrid"}
    assert kept({"remote_policy": "any"}) == {"remote", "hybrid", "onsite"}
    assert kept({}) == {"remote", "hybrid"}


def test_each_sections_switches_add_up_rather_than_narrow_each_other():
    rules = filters(sections=[{"platform": "linkedin", "keywords": ["go"], "workplaces": ["remote"]},
                              {"platform": "all", "keywords": ["rust"], "workplaces": ["onsite"]}])
    assert drops(Item(workplace="onsite"), rules) is None
    assert drops(Item(workplace="hybrid"), rules) == "workplace"


def test_a_job_site_s_own_words_for_a_way_of_working_are_read_as_it():
    """Boards name them in their own words and languages. A job titled in Spanish as hybrid is hybrid,
    and a remote one remote, for someone who switched on only on-site work — and a word that also
    names something else ("Front Office") is not read as one."""
    rules = switched("onsite")
    assert drops(Item(title=".NET Developer – Madrid (Híbrido)", location="Spain"), rules) == "workplace"  # noqa: RUF001
    assert drops(Item(title="Backend Engineer (100% remoto)", location="Spain"), rules) == "workplace"
    assert drops(Item(title="Android Developer (praca zdalna)", location="Wrocław"), rules) == "workplace"
    assert drops(Item(title="Front Office Supervisor", location="Sligo, Ireland"), switched("remote")) is None


def test_a_linkedin_listing_at_the_display_cap_is_dropped_as_stale():
    assert drops(Item(applicants=200)) == "applicant_glut"


def test_the_same_count_on_another_source_is_not():
    """Only LinkedIn reports a count worth trusting."""
    assert drops(Item(source="greenhouse", applicants=200)) is None


def test_a_user_who_disabled_the_cap_sees_them():
    assert drops(Item(applicants=500), filters(applicant_cap=0)) is None


def test_applicant_glut_needs_a_count_to_act_on():
    assert applicant_glut(source="linkedin", applicants=None, cap=200) is False


# ── the age window the user asked for ─────────────────────────────────────────────────────────

def test_the_age_window_is_enforced_in_sql_before_anything_is_scored():
    """The harvester fetches at the widest window any account asked for."""
    tight = filters(max_age_hours=6)
    assert tight.max_age_hours == 6
    assert tight.fresh_since(now=NOW) == NOW - timedelta(hours=6)
    # And that it is not silently one of the checks that moved into `unwanted`.
    assert drops(Item(posted_at=NOW - timedelta(hours=300)), tight) is None


def test_a_posting_inside_the_window_is_kept():
    fresh = Item(posted_at=NOW - timedelta(hours=3))
    assert drops(fresh, filters(max_age_hours=24)) is None


def test_the_window_is_not_one_of_the_checks_that_moved_into_unwanted():
    """It is a SQL condition, so `unwanted` must not answer for it a second time."""
    item = Item(posted_at=NOW - timedelta(hours=100))
    assert drops(item, filters(max_age_hours=6)) is None


def test_a_posting_with_no_dates_at_all_is_kept():
    """Unknown means keep."""
    item = Item(posted_at=None)
    assert drops(item, filters(max_age_hours=6)) is None


def test_the_window_is_the_operators_and_is_never_defaulted_in_the_filter():
    """A window nobody set is a window nobody can change; it comes from `job_max_age_hours`."""
    from libs.settings import DEFAULTS

    assert UserFilters.from_config({}, max_age_hours=48).max_age_hours == 48
    assert "job_max_age_hours" in DEFAULTS


def test_a_v1_config_keeps_its_window_and_its_exclusions():
    """A v1 row is flat — `posted_within_hours` and `exclude_keywords` at the top level."""
    rules = UserFilters.from_config({
        "keywords": ["mechanical engineer"],
        "exclude_keywords": ["recruitment agency"],
        "languages": [],
    }, max_age_hours=72)
    assert rules.max_age_hours == 72
    assert rules.exclude == ("recruitment agency",)


def test_the_age_window_is_not_the_notification_interval():
    """They are different questions and were being conflated."""
    from libs.contracts.config import SearchSection

    assert "posted_within_hours" in SearchSection.model_fields
    assert "notify_interval_min" not in SearchSection.model_fields


# ── the terms the user asked never to see ─────────────────────────────────────────────────────

def test_an_excluded_keyword_is_a_hard_no_before_scoring():
    """They were honoured only by the cold-start keyword scorer."""
    item = Item(title="Contract Backend Engineer")
    assert drops(item, filters(sections=[{"exclude_keywords": ["contract"]}])) == "excluded_keyword"


def test_an_excluded_keyword_matches_the_company_too():
    item = Item(title="Backend Engineer", company="Hays Recruitment")
    assert drops(item, filters(sections=[{"exclude_keywords": ["hays"]}])) == "excluded_keyword"


def test_an_excluded_term_in_the_description_does_not_drop_the_job():
    """A posting that merely mentions the term in a benefits paragraph is not the job being excluded."""
    item = Item(title="Backend Engineer",
                description="We work with contract staff occasionally. " * 10)
    assert drops(item, filters(sections=[{"exclude_keywords": ["contract"]}])) is None


def test_no_excluded_keywords_drops_nothing():
    assert drops(Item(), filters(sections=[{"exclude_keywords": []}])) is None


# ── dating the feed before filtering it ───────────────────────────────────────────────────────

MONTH_OLD = ('<span class="posted-time-ago__text"> Reposted 1 month ago </span>'
             '<span class="num-applicants__caption"> 180 applicants </span>' + "x" * 300)


def test_dating_an_undated_linkedin_job_is_what_makes_the_age_window_answerable():
    """The leak that kept "Reposted 1 month ago" in the feed."""
    from libs.corpus import postings

    dated = postings.parse_posted_at(MONTH_OLD, now=NOW)
    assert dated is not None, "an undated row stays undated and the window has nothing to test"
    assert dated < NOW - timedelta(days=20)

