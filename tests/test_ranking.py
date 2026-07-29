"""Opportunity ranking and the eligibility filters.

**The distinction under test is score versus opportunity.** Score answers "does this job fit"; this
answers "is it worth applying to now", and the platform had been sorting the feed by the first. The
tests that matter are the ones where those two orderings disagree — a perfect-fit posting three
weeks old against a decent one from an hour ago.

The filters are tested from the other direction: what they refuse to drop. Every one fails open,
because a false drop removes a real job from a feed silently and the user never learns it existed.
"""
from __future__ import annotations

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
from services.notifier.eligibility import Eligibility, is_eligible, rank, select

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
    posted_at: datetime | None = NOW - timedelta(hours=1)
    applicants: int | None = 5
    easy_apply: bool | None = None
    reposted: bool | None = None
    workplace: str | None = None


def rules(**overrides) -> Eligibility:
    return Eligibility.from_config({"languages": ["en"], **overrides})


# ── the buckets ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("hours", "bucket"), [(1, 0), (12, 1), (48, 2), (200, 3)])
def test_freshness_falls_into_the_reference_s_buckets(hours, bucket):
    assert fresh_bucket(posted_at=NOW - timedelta(hours=hours), now=NOW) == bucket


def test_a_posting_with_no_date_falls_back_to_when_we_found_it():
    """LinkedIn frequently gives no posted date. When we found it is a worse answer than when it
    was posted, but it is a real one — the alternative is calling every such job old."""
    assert fresh_bucket(posted_at=None, first_seen_at=NOW - timedelta(hours=12), now=NOW) == 1


def test_a_posting_with_no_dates_at_all_is_treated_as_old():
    assert fresh_bucket(posted_at=None, first_seen_at=None, now=NOW) == 3


def test_a_repost_is_the_oldest_thing_there_is_whatever_its_timestamp_says():
    """**LinkedIn resurfaces stale listings with a fresh clock.** Trusting that timestamp would put
    a job that has been up for a month at the top of the feed."""
    assert fresh_bucket(posted_at=NOW - timedelta(minutes=5), reposted=True, now=NOW) == 3


@pytest.mark.parametrize(("count", "bucket"), [(0, 0), (9, 0), (10, 1), (49, 1), (50, 2), (200, 3)])
def test_applicant_counts_bucket_at_the_reference_s_edges(count, bucket):
    assert applicant_bucket(count) == bucket


def test_an_unknown_applicant_count_ranks_worst_rather_than_best():
    """Conservative for *ranking*, where being wrong costs ordering. The filters go the other way,
    because being wrong there deletes the job."""
    assert applicant_bucket(None) == 3


# ── the score ─────────────────────────────────────────────────────────────────────────────────

def test_the_best_possible_job_scores_a_hundred():
    assert opportunity_score(posted_at=NOW, applicants=0, now=NOW) == 100


def test_a_closed_listing_scores_zero_however_good_it_is():
    """You cannot apply to it. Nothing else about the posting can outweigh that."""
    assert opportunity_score(posted_at=NOW, applicants=0, accepting=False, now=NOW) == 0


def test_youth_and_scarcity_are_weighted_equally():
    """Fifty points each, which is the reference's judgement: being early and being one of few are
    the same size of advantage."""
    young_and_crowded = opportunity_score(posted_at=NOW, applicants=500, now=NOW)
    old_and_empty = opportunity_score(posted_at=NOW - timedelta(days=30), applicants=0, now=NOW)
    assert young_and_crowded == old_and_empty


def test_easy_apply_moves_nothing():
    """It shows on the card as a tag. It stopped earning priority in the reference after the
    applications it produced went nowhere."""
    with_easy = opportunity_score(posted_at=NOW, applicants=1, now=NOW)
    assert with_easy == opportunity_score(posted_at=NOW, applicants=1, now=NOW)


# ── ordering, where score and opportunity disagree ────────────────────────────────────────────

def test_a_fresh_good_job_outranks_a_stale_perfect_one():
    """**The reason this module exists.** Sorted by score the second wins; sorted by opportunity the
    first does, because it is the one that can still be won."""
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
    assert is_eligible(german, rules()) == (False, "language")


def test_a_description_too_short_to_detect_is_kept():
    """Fails open. A three-word snippet is not evidence of anything."""
    assert is_eligible(Item(description="Engineer wanted"), rules())[0] is True


def test_an_account_that_never_stated_a_language_keeps_everything():
    """Otherwise a user who skipped one onboarding question would have their entire feed silently
    emptied."""
    german = Item(description="Wir suchen einen erfahrenen Softwareentwickler für unser Team. " * 3)
    assert is_eligible(german, Eligibility.from_config({}))[0] is True


def test_an_explicit_onsite_posting_outside_the_allowed_countries_is_dropped():
    item = Item(workplace="onsite", location="Paris, France")
    assert is_eligible(item, rules(onsite_locations=["hungary"])) == (False, "workplace")


def test_an_onsite_posting_where_the_user_will_go_is_kept():
    item = Item(workplace="onsite", location="Budapest, Hungary")
    assert is_eligible(item, rules(onsite_locations=["hungary"]))[0] is True


def test_a_job_whose_workplace_is_unknown_is_never_dropped():
    """The filter only ever drops an *explicit* on-site tag. Everything else fails open."""
    assert is_eligible(Item(workplace=None, location="Paris, France"),
                       rules(onsite_locations=["hungary"]))[0] is True


def test_a_linkedin_listing_at_the_display_cap_is_dropped_as_stale():
    assert is_eligible(Item(applicants=200), rules()) == (False, "applicant_glut")


def test_the_same_count_on_another_source_is_not():
    """Only LinkedIn reports a count worth trusting, and only there does the 200 cap mean
    "over-subscribed" rather than "exactly two hundred"."""
    assert is_eligible(Item(source="greenhouse", applicants=200), rules())[0] is True


def test_a_user_who_disabled_the_cap_sees_them():
    assert is_eligible(Item(applicants=500), rules(applicant_cap=0))[0] is True


def test_applicant_glut_needs_a_count_to_act_on():
    assert applicant_glut(source="linkedin", applicants=None, cap=200) is False


# ── selection ─────────────────────────────────────────────────────────────────────────────────

def test_selection_filters_ranks_and_caps_in_that_order():
    items = [
        Item(job_id=1, score=99, posted_at=NOW - timedelta(days=30), applicants=180),
        Item(job_id=2, score=70, posted_at=NOW - timedelta(minutes=10), applicants=2),
        Item(job_id=3, score=95, applicants=200),                        # glut
    ]
    kept, dropped = select(items, rules(), limit=2)

    assert [item.job_id for item in kept] == [2, 1]
    assert dropped == {"applicant_glut": 1}


def test_the_drop_tally_says_why_a_digest_was_small():
    """"Delivered 2" is alarming and unactionable. "Dropped 38 for language" is a config problem
    somebody can fix in a minute."""
    german = "Wir suchen einen erfahrenen Softwareentwickler für unser Team in Berlin. " * 3
    items = [Item(job_id=index, description=german) for index in range(3)]
    kept, dropped = select(items, rules(), limit=10)

    assert kept == []
    assert dropped == {"language": 3}
