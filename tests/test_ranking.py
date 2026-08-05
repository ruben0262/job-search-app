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
    # Relative to the module's fixed NOW, and **every call must pass `now=NOW`**. The filters
    # default to the real clock, so a fixture pinned to a date and a call that is not will agree
    # for a day and then start failing at midnight — which is exactly what happened.
    posted_at: datetime | None = NOW - timedelta(hours=1)
    applicants: int | None = 5
    easy_apply: bool | None = None
    reposted: bool | None = None
    workplace: str | None = None


def rules(*, applicant_cap: int | None = 200, **overrides) -> Eligibility:
    """One user's filters. `applicant_cap` is separate because it is an operator limit resolved
    from `libs.limits`, not a field on their config — passing it in the config dict was how it
    stayed invisible to the panel that is supposed to own it."""
    return Eligibility.from_config({"languages": ["en"], **overrides},
                                   applicant_cap=applicant_cap)


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
    assert is_eligible(german, rules(), now=NOW) == (False, "language")


def test_a_description_too_short_to_detect_is_kept():
    """Fails open. A three-word snippet is not evidence of anything."""
    assert is_eligible(Item(description="Engineer wanted"), rules(), now=NOW)[0] is True


def test_an_account_that_never_stated_a_language_keeps_everything():
    """Otherwise a user who skipped one onboarding question would have their entire feed silently
    emptied."""
    german = Item(description="Wir suchen einen erfahrenen Softwareentwickler für unser Team. " * 3)
    assert is_eligible(german, Eligibility.from_config({}), now=NOW)[0] is True


def test_an_explicit_onsite_posting_outside_the_allowed_countries_is_dropped():
    item = Item(workplace="onsite", location="Paris, France")
    assert is_eligible(item, rules(onsite_locations=["hungary"]), now=NOW) == (False, "workplace")


def test_an_onsite_posting_where_the_user_will_go_is_kept():
    item = Item(workplace="onsite", location="Budapest, Hungary")
    assert is_eligible(item, rules(onsite_locations=["hungary"]), now=NOW)[0] is True


def test_a_job_whose_workplace_is_unknown_is_never_dropped():
    """The filter only ever drops an *explicit* on-site tag. Everything else fails open."""
    assert is_eligible(Item(workplace=None, location="Paris, France"),
                       rules(onsite_locations=["hungary"]), now=NOW)[0] is True


def test_a_linkedin_listing_at_the_display_cap_is_dropped_as_stale():
    assert is_eligible(Item(applicants=200), rules(), now=NOW) == (False, "applicant_glut")


def test_the_same_count_on_another_source_is_not():
    """Only LinkedIn reports a count worth trusting, and only there does the 200 cap mean
    "over-subscribed" rather than "exactly two hundred"."""
    assert is_eligible(Item(source="greenhouse", applicants=200), rules(), now=NOW)[0] is True


def test_a_user_who_disabled_the_cap_sees_them():
    assert is_eligible(Item(applicants=500), rules(applicant_cap=0), now=NOW)[0] is True


def test_applicant_glut_needs_a_count_to_act_on():
    assert applicant_glut(source="linkedin", applicants=None, cap=200) is False


# ── selection ─────────────────────────────────────────────────────────────────────────────────

def test_selection_filters_ranks_and_caps_in_that_order():
    # A week's window, so the stale job is dropped for being crowded rather than for being old —
    # the age filter has its own tests and this one is about ordering.
    week = rules(sections=[{"posted_within_hours": 168}])
    items = [
        Item(job_id=1, score=99, posted_at=NOW - timedelta(hours=100), applicants=180),
        Item(job_id=2, score=70, posted_at=NOW - timedelta(minutes=10), applicants=2),
        Item(job_id=3, score=95, applicants=200),                        # glut
    ]
    kept, dropped = select(items, week, limit=2, now=NOW)

    assert [item.job_id for item in kept] == [2, 1]
    assert dropped == {"applicant_glut": 1}


def test_the_drop_tally_says_why_a_digest_was_small():
    """"Delivered 2" is alarming and unactionable. "Dropped 38 for language" is a config problem
    somebody can fix in a minute."""
    german = "Wir suchen einen erfahrenen Softwareentwickler für unser Team in Berlin. " * 3
    items = [Item(job_id=index, description=german) for index in range(3)]
    kept, dropped = select(items, rules(), limit=10, now=NOW)

    assert kept == []
    assert dropped == {"language": 3}


# ── the age window the user asked for ─────────────────────────────────────────────────────────

def test_a_posting_older_than_the_users_window_is_dropped():
    """**The harvester fetches at the widest window any account asked for**, because the corpus is
    shared and one fetch serves everyone. Without this, a user who asked for six hours was being
    sent whatever the most patient account on the platform wanted."""
    old = Item(posted_at=NOW - timedelta(hours=30))
    assert is_eligible(old, rules(sections=[{"posted_within_hours": 24}]),
                       now=NOW) == (False, "too_old")


def test_a_posting_inside_the_window_is_kept():
    fresh = Item(posted_at=NOW - timedelta(hours=3))
    assert is_eligible(fresh, rules(sections=[{"posted_within_hours": 24}]), now=NOW)[0] is True


def test_the_widest_of_several_sections_wins():
    """A user with six hours on one search and a week on another asked for both, and a job cannot
    be attributed back to the section that found it. The generous reading is the only one that
    cannot silently delete something they asked for."""
    item = Item(posted_at=NOW - timedelta(hours=100))
    both = rules(sections=[{"posted_within_hours": 6}, {"posted_within_hours": 168}])
    assert both.max_age_hours == 168
    assert is_eligible(item, both, now=NOW)[0] is True


def test_the_age_filter_falls_back_to_when_we_found_it():
    item = Item(posted_at=None)
    tight = rules(sections=[{"posted_within_hours": 6}])
    assert is_eligible(item, tight, first_seen=NOW - timedelta(hours=40), now=NOW)[0] is False
    assert is_eligible(item, tight, first_seen=NOW - timedelta(hours=1), now=NOW)[0] is True


def test_a_posting_with_no_dates_at_all_is_kept():
    """**Unknown means keep.** Most sources report no posted date, so dropping on an absent field
    would silently empty the feed of every one of them."""
    item = Item(posted_at=None)
    assert is_eligible(item, rules(sections=[{"posted_within_hours": 6}]), now=NOW)[0] is True


def test_an_account_with_no_sections_gets_the_default_window():
    from services.notifier.eligibility import DEFAULT_MAX_AGE_HOURS

    assert Eligibility.from_config({}).max_age_hours == DEFAULT_MAX_AGE_HOURS


def test_a_v1_config_keeps_its_window_and_its_exclusions():
    """A v1 row is flat — `posted_within_hours` and `exclude_keywords` at the top level — and
    `from_config` reads sections. Found live: the account that asked for 72 hours and typed
    exclusions was getting the 24-hour default and none of them."""
    rules = Eligibility.from_config({
        "keywords": ["mechanical engineer"],
        "posted_within_hours": 72,
        "exclude_keywords": ["recruitment agency"],
        "languages": [],
    })
    assert rules.max_age_hours == 72
    assert rules.exclude_keywords == ("recruitment agency",)


def test_the_age_window_is_not_the_notification_interval():
    """They are different questions and were being conflated. `notify_interval_min` is how often
    the user hears from us; this is how old a posting may be to be worth hearing about."""
    from libs.contracts.config import SearchSection

    assert "posted_within_hours" in SearchSection.model_fields
    assert "notify_interval_min" not in SearchSection.model_fields


# ── the terms the user asked never to see ─────────────────────────────────────────────────────

def test_an_excluded_keyword_is_a_hard_no_at_delivery():
    """**They were honoured only by the cold-start keyword scorer**, so they quietly stopped
    applying the moment an account got a trained model. "I told it never to show me contract roles
    and it started again" is indistinguishable from the product ignoring them — because it was."""
    item = Item(title="Contract Backend Engineer")
    assert is_eligible(item, rules(sections=[{"exclude_keywords": ["contract"]}]),
                       now=NOW) == (False, "excluded_keyword")


def test_an_excluded_keyword_matches_the_company_too():
    item = Item(title="Backend Engineer", company="Hays Recruitment")
    assert is_eligible(item, rules(sections=[{"exclude_keywords": ["hays"]}]), now=NOW)[0] is False


def test_an_excluded_term_in_the_description_does_not_drop_the_job():
    """A posting that merely mentions the term in a benefits paragraph is not the job being
    excluded. Matching the body turned "no recruitment agencies" into a filter that removed most
    of a feed."""
    item = Item(title="Backend Engineer",
                description="We work with contract staff occasionally. " * 10)
    assert is_eligible(item, rules(sections=[{"exclude_keywords": ["contract"]}]), now=NOW)[0] is True


def test_no_excluded_keywords_drops_nothing():
    assert is_eligible(Item(), rules(sections=[{"exclude_keywords": []}]), now=NOW)[0] is True


# ── dating the feed before filtering it ───────────────────────────────────────────────────────

class DatingClient:
    """Answers the guest fragment for any job asked about."""

    def __init__(self, page: str):
        self.page = page
        self.asked: list[str] = []

    def get(self, url, **_):
        self.asked.append(url)
        return type("R", (), {"text": self.page, "status_code": 200, "url": url})()

    def stream(self, _method, url, **_):
        from contextlib import nullcontext
        streamed = type("S", (), {"text": self.page, "status_code": 200,
                                  "iter_bytes": lambda self: iter([self.text.encode()])})()
        return nullcontext(streamed)


MONTH_OLD = ('<span class="posted-time-ago__text"> Reposted 1 month ago </span>'
             '<span class="num-applicants__caption"> 180 applicants </span>' + "x" * 300)


def test_an_undated_linkedin_job_is_only_dropped_once_it_has_been_dated():
    """**The leak that kept "Reposted 1 month ago" in the feed.** LinkedIn reports no posted time
    to the listing API, so the age filter had nothing to test and correctly failed open. Dating the
    job first is what turns "we never asked" into an answer."""
    from libs.corpus import postings

    undated = Item(posted_at=None, source="linkedin")
    tight = rules(sections=[{"posted_within_hours": 6}])

    # Undated and recently found: nothing to drop it on, so it goes out.
    assert is_eligible(undated, tight, first_seen=NOW, now=NOW)[0] is True

    # Dated from the posting's own page, it is a month old and fails the same filter.
    undated.posted_at = postings.parse_posted_at(MONTH_OLD, now=NOW)
    assert undated.posted_at is not None
    assert is_eligible(undated, tight, first_seen=NOW, now=NOW) == (False, "too_old")


def test_a_closed_posting_found_at_send_time_is_dropped_and_not_failed_open():
    """Closed is a fact about the posting, not a preference. It does not get the benefit of the
    doubt the language and workplace filters get."""
    item = Item()
    item.closed = True
    assert is_eligible(item, rules(), now=NOW) == (False, "closed")


# ── what a job is embedded from ───────────────────────────────────────────────────────────────

def test_the_title_leads_the_embedded_text():
    """**The single strongest statement of what a job is**, and the part that survives the
    encoder's truncation — but only if it leads. `bge-base` reads about 2,000 characters and some
    boards average 6,662, so a title appended at the end would be cut off every time."""
    from libs.corpus.embedtext import build_text

    text = build_text("Backend Engineer", "We need Python and Postgres.")
    assert text.startswith("Backend Engineer")


def test_the_embedded_text_carries_no_employer_or_geography():
    """The reference's reason, and it is about generalisation: a model that has seen the employer
    name learns employers, scores unseen companies badly, and encodes geography as fit."""
    from libs.corpus.embedtext import build_text

    text = build_text("Backend Engineer", "Work on our platform.")
    assert "Acme" not in text and "Berlin" not in text


def test_the_text_is_capped():
    from libs.corpus.embedtext import MAX_CHARS, build_text

    assert len(build_text("Engineer", "x" * 50_000)) == MAX_CHARS


def test_a_job_with_no_description_still_embeds_its_title():
    """Most ATS boards give a title and a link and nothing else on the first pass. Embedding an
    empty string would put every one of them at the same point in the space."""
    from libs.corpus.embedtext import build_text

    assert build_text("Backend Engineer", None) == "Backend Engineer"
    assert build_text("Backend Engineer", "") == "Backend Engineer"


def test_the_text_mode_is_recorded_in_the_embedding_version():
    """**Two vectors of the same width built from different text are indistinguishable by
    inspection.** Folding the mode into the version makes a stale vector refuse to be scored rather
    than score confidently and wrongly."""
    from libs.corpus.embedtext import embedding_version

    assert embedding_version("dim=768|norm=1") == "dim=768|norm=1|text=title_desc"
    assert embedding_version("dim=768|norm=1", mode="desc").endswith("|text=desc")
