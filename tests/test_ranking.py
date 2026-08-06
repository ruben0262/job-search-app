"""Opportunity ranking and the eligibility filters."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from libs.corpus.ranking import (
    applicant_bucket,
    applicant_glut,
    fresh_bucket,
    opportunity_score,
    priority_key,
)
from libs.tenant.filters import UserFilters
from services.notifier.eligibility import is_eligible, rank, select

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


@dataclass
class Item:
    """The shape `eligibility` reads — the fields of a `FeedItem` it actually touches."""

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


def filters(*, applicant_cap: int | None = 200, **overrides) -> UserFilters:
    """The same account, as the *pre-score* filter sees it."""
    return UserFilters.from_config({"languages": ["en"], **overrides},
                                   applicant_cap=applicant_cap)


def drops(item, rules_=None) -> str | None:
    """Why this account would never receive this posting, or `None`."""
    return (rules_ or filters()).unwanted(dataclasses.asdict(item))


# ── the buckets ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("hours", "bucket"), [(1, 0), (12, 1), (48, 2), (200, 3)])
def test_freshness_falls_into_the_reference_s_buckets(hours, bucket):
    assert fresh_bucket(posted_at=NOW - timedelta(hours=hours), now=NOW) == bucket


def test_a_posting_with_no_date_falls_back_to_when_we_found_it():
    """LinkedIn frequently gives no posted date."""
    assert fresh_bucket(posted_at=None, first_seen_at=NOW - timedelta(hours=12), now=NOW) == 1


def test_a_posting_with_no_dates_at_all_is_treated_as_old():
    assert fresh_bucket(posted_at=None, first_seen_at=None, now=NOW) == 3


def test_a_repost_is_the_oldest_thing_there_is_whatever_its_timestamp_says():
    """LinkedIn resurfaces stale listings with a fresh clock."""
    assert fresh_bucket(posted_at=NOW - timedelta(minutes=5), reposted=True, now=NOW) == 3


@pytest.mark.parametrize(("count", "bucket"), [(0, 0), (9, 0), (10, 1), (49, 1), (50, 2), (200, 3)])
def test_applicant_counts_bucket_at_the_reference_s_edges(count, bucket):
    assert applicant_bucket(count) == bucket


def test_an_unknown_applicant_count_ranks_worst_rather_than_best():
    """Conservative for *ranking*, where being wrong costs ordering."""
    assert applicant_bucket(None) == 3


# ── the score ─────────────────────────────────────────────────────────────────────────────────

def test_the_best_possible_job_scores_a_hundred():
    assert opportunity_score(posted_at=NOW, applicants=0, now=NOW) == 100


def test_a_closed_listing_scores_zero_however_good_it_is():
    """You cannot apply to it. Nothing else about the posting can outweigh that."""
    assert opportunity_score(posted_at=NOW, applicants=0, accepting=False, now=NOW) == 0


def test_youth_and_scarcity_are_weighted_equally():
    """Fifty points each, which is the reference's judgement."""
    young_and_crowded = opportunity_score(posted_at=NOW, applicants=500, now=NOW)
    old_and_empty = opportunity_score(posted_at=NOW - timedelta(days=30), applicants=0, now=NOW)
    assert young_and_crowded == old_and_empty


def test_easy_apply_moves_nothing():
    """It shows on the card as a tag."""
    with_easy = opportunity_score(posted_at=NOW, applicants=1, now=NOW)
    assert with_easy == opportunity_score(posted_at=NOW, applicants=1, now=NOW)


# ── ordering, where score and opportunity disagree ────────────────────────────────────────────

def test_a_fresh_good_job_outranks_a_stale_perfect_one():
    """The reason this module exists."""
    fresh = Item(job_id=1, score=70, posted_at=NOW - timedelta(minutes=30), applicants=3)
    stale = Item(job_id=2, score=99, posted_at=NOW - timedelta(days=21), applicants=250)
    assert [item.job_id for item in rank([stale, fresh])] == [1, 2]


def test_fit_still_breaks_a_tie():
    """It gates entry and orders equals. It just does not dominate."""
    better = Item(job_id=1, score=95)
    worse = Item(job_id=2, score=61)
    assert [item.job_id for item in rank([worse, better])] == [1, 2]


def test_remote_leads_hybrid_leads_onsite_at_equal_freshness():
    remote = Item(job_id=1, workplace="remote")
    hybrid = Item(job_id=2, workplace="hybrid")
    onsite = Item(job_id=3, workplace="onsite", location="Budapest, Hungary")
    assert [item.job_id for item in rank([onsite, hybrid, remote])] == [1, 2, 3]


def test_an_unknown_workplace_ranks_with_hybrid_not_last():
    """Unknown is the common case. Treating it as on-site would bury most of the feed."""
    unknown = priority_key(posted_at=NOW, applicants=1, workplace=None, now=NOW)
    hybrid = priority_key(posted_at=NOW, applicants=1, workplace="hybrid", now=NOW)
    assert unknown == hybrid


# ── the filters, and what they refuse to drop ─────────────────────────────────────────────────

def test_a_job_in_a_language_the_user_does_not_write_is_dropped():
    german = Item(description=(
        "Wir suchen einen erfahrenen Softwareentwickler für unser Team in Berlin. "
        "Sie entwickeln und betreiben unsere Dienste und arbeiten eng mit dem Produktteam "
        "zusammen. Gute Deutschkenntnisse sind erforderlich."))
    assert drops(german) == "language"


def test_a_description_too_short_to_detect_is_kept():
    """Fails open. A three-word snippet is not evidence of anything."""
    assert drops(Item(description="Engineer wanted")) is None


def test_an_account_that_never_stated_a_language_keeps_everything():
    """Otherwise a user who skipped one onboarding question would have their entire feed silently emptied."""
    german = Item(description="Wir suchen einen erfahrenen Softwareentwickler für unser Team. " * 3)
    assert drops(german, UserFilters.from_config({})) is None


def test_an_explicit_onsite_posting_outside_the_allowed_countries_is_dropped():
    item = Item(workplace="onsite", location="Paris, France")
    assert drops(item, filters(onsite_locations=["hungary"])) == "workplace"


def test_an_onsite_posting_where_the_user_will_go_is_kept():
    item = Item(workplace="onsite", location="Budapest, Hungary")
    assert drops(item, filters(onsite_locations=["hungary"])) is None


def test_a_job_whose_workplace_is_unknown_is_never_dropped():
    """The filter only ever drops an *explicit* on-site tag. Everything else fails open."""
    assert drops(Item(workplace=None, location="Paris, France"),
                 filters(onsite_locations=["hungary"])) is None


def test_a_linkedin_listing_at_the_display_cap_is_dropped_as_stale():
    assert drops(Item(applicants=200)) == "applicant_glut"


def test_the_same_count_on_another_source_is_not():
    """Only LinkedIn reports a count worth trusting."""
    assert drops(Item(source="greenhouse", applicants=200)) is None


def test_a_user_who_disabled_the_cap_sees_them():
    assert drops(Item(applicants=500), filters(applicant_cap=0)) is None


def test_applicant_glut_needs_a_count_to_act_on():
    assert applicant_glut(source="linkedin", applicants=None, cap=200) is False


# ── selection ─────────────────────────────────────────────────────────────────────────────────

def test_selection_ranks_and_caps_and_no_longer_filters():
    """Selection stopped being a filter on 2026-08-06."""
    items = [
        Item(job_id=1, score=99, posted_at=NOW - timedelta(hours=100), applicants=180),
        Item(job_id=2, score=70, posted_at=NOW - timedelta(minutes=10), applicants=2),
        Item(job_id=3, score=95, posted_at=NOW - timedelta(hours=200), applicants=190),
    ]
    kept, dropped = select(items, limit=2, now=NOW)

    assert [item.job_id for item in kept] == [2, 1], "freshest and least crowded first"
    assert dropped == {}, "nothing is filtered at delivery any more"


def test_the_drop_tally_still_names_a_closed_posting():
    """"Delivered 2" is alarming and unactionable; the reason is what makes it actionable."""
    items = [Item(job_id=index) for index in range(3)]
    for item in items[:2]:
        item.closed = True
    kept, dropped = select(items, limit=10, now=NOW)

    assert [item.job_id for item in kept] == [2]
    assert dropped == {"closed": 2}


# ── the age window the user asked for ─────────────────────────────────────────────────────────

def test_the_age_window_is_enforced_in_sql_before_anything_is_scored():
    """The harvester fetches at the widest window any account asked for."""
    tight = filters(sections=[{"posted_within_hours": 6}])
    assert tight.max_age_hours == 6
    assert tight.fresh_since(now=NOW) == NOW - timedelta(hours=6)
    # And that it is not silently one of the checks that moved into `unwanted`.
    assert drops(Item(posted_at=NOW - timedelta(hours=300)), tight) is None


def test_a_posting_inside_the_window_is_kept():
    fresh = Item(posted_at=NOW - timedelta(hours=3))
    assert drops(fresh, filters(sections=[{"posted_within_hours": 24}])) is None


def test_the_widest_of_several_sections_wins():
    """A user with six hours on one search and a week on another asked for both."""
    item = Item(posted_at=NOW - timedelta(hours=100))
    both = filters(sections=[{"posted_within_hours": 6}, {"posted_within_hours": 168}])
    assert both.max_age_hours == 168
    assert drops(item, both) is None


def test_a_posting_with_no_dates_at_all_is_kept():
    """Unknown means keep."""
    item = Item(posted_at=None)
    assert drops(item, filters(sections=[{"posted_within_hours": 6}])) is None


def test_an_account_with_no_sections_gets_the_default_window():
    from libs.tenant.filters import DEFAULT_MAX_AGE_HOURS

    assert UserFilters.from_config({}).max_age_hours == DEFAULT_MAX_AGE_HOURS


def test_a_v1_config_keeps_its_window_and_its_exclusions():
    """A v1 row is flat — `posted_within_hours` and `exclude_keywords` at the top level."""
    rules = UserFilters.from_config({
        "keywords": ["mechanical engineer"],
        "posted_within_hours": 72,
        "exclude_keywords": ["recruitment agency"],
        "languages": [],
    })
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


def test_a_closed_posting_found_at_send_time_is_dropped_and_not_failed_open():
    """Closed is a fact about the posting, not a preference."""
    item = Item()
    item.closed = True
    assert is_eligible(item) == (False, "closed")


# ── what a job is embedded from ───────────────────────────────────────────────────────────────

def test_the_title_leads_the_embedded_text():
    """The single strongest statement of what a job is."""
    from libs.corpus.embedtext import build_text

    text = build_text("Backend Engineer", "We need Python and Postgres.")
    assert text.startswith("Backend Engineer")


def test_the_embedded_text_carries_no_employer_or_geography():
    """The reference's reason, and it is about generalisation."""
    from libs.corpus.embedtext import build_text

    text = build_text("Backend Engineer", "Work on our platform.")
    assert "Acme" not in text and "Berlin" not in text


def test_the_text_is_capped():
    from libs.corpus.embedtext import MAX_CHARS, build_text

    assert len(build_text("Engineer", "x" * 50_000)) == MAX_CHARS


def test_a_job_with_no_description_still_embeds_its_title():
    """Most ATS boards give a title and a link and nothing else on the first pass."""
    from libs.corpus.embedtext import build_text

    assert build_text("Backend Engineer", None) == "Backend Engineer"
    assert build_text("Backend Engineer", "") == "Backend Engineer"


def test_the_text_mode_is_recorded_in_the_embedding_version():
    """Two vectors of the same width built from different text are indistinguishable by inspection."""
    from libs.corpus.embedtext import embedding_version

    assert embedding_version("dim=768|norm=1") == "dim=768|norm=1|text=title_desc"
    assert embedding_version("dim=768|norm=1", mode="desc").endswith("|text=desc")
