"""Opportunity ranking and the eligibility filters."""
from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from libs.db import insert
from libs.db.models import CorpusJob
from libs.tenant.filters import UserFilters, candidate_conditions

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


@pytest.fixture()
def drops(db):
    """Whether this account would never receive this posting: asked of the database, as every match asks
    (`candidate_conditions`), with the posting stored as the harvest stores one. An `Item` as it stands is
    kept, so a test that changes one thing about it and sees it dropped has named the reason."""
    def dropped(item, rules_=None) -> bool:
        row = {name: value for name, value in dataclasses.asdict(item).items()
               if name not in ("job_id", "score")}
        found = item.posted_at or NOW - timedelta(hours=1)
        job = insert(db, CorpusJob(url=f"https://example.com/{uuid.uuid4()}", first_seen_at=found,
                                   described_at=found, **row))
        return not db[CorpusJob.COLLECTION].count_documents(
            {"$and": [{"_id": job.id}, *candidate_conditions(rules_ or filters(), now=NOW)]})

    assert not dropped(Item()), "the posting every test starts from must be one this account receives"
    return dropped


# ── the buckets ───────────────────────────────────────────────────────────────────────────────

# ── the score ─────────────────────────────────────────────────────────────────────────────────

# ── ordering, where score and opportunity disagree ────────────────────────────────────────────

# ── the filters, and what they refuse to drop ─────────────────────────────────────────────────

def test_a_job_in_a_language_the_user_does_not_write_is_dropped(drops):
    assert drops(Item(language="de"))


def test_the_filter_reads_the_stored_code_and_never_runs_a_detector(drops):
    """Detection is a property of the posting: once at describe time, not per reader per pass. A German
    description with the stored code saying English is judged by the column."""
    german_text = Item(language="en", description="Wir suchen einen Softwareentwickler. " * 5)
    assert not drops(german_text)


def test_a_posting_whose_language_could_not_be_read_is_kept(drops):
    """Fails open. Unknown is not evidence of anything."""
    assert not drops(Item(language=None))


def test_an_account_that_never_stated_a_language_keeps_everything(drops):
    """Otherwise a user who skipped one onboarding question would have their entire feed silently emptied."""
    assert not drops(Item(language="de"), UserFilters.from_config({}, max_age_hours=24))


def switched(*ways: str) -> UserFilters:
    return filters(sections=[{"keywords": ["backend engineer"], "workplaces": list(ways)}])


def test_remote_and_hybrid_drop_an_explicit_onsite_job_wherever_it_is(drops):
    """Was: on-site allowed only in Hungary, the reference owner's default, for every account."""
    for place in ("Budapest, Hungary", "Dublin, Ireland"):
        assert drops(Item(workplace="onsite", location=place), switched("remote", "hybrid"))


def test_every_switch_on_keeps_an_onsite_job_anywhere_and_place_is_the_country_filter_s_job(drops):
    assert not drops(Item(workplace="onsite", location="Dublin, Ireland"),
                     switched("remote", "hybrid", "onsite"))


def test_remote_alone_drops_hybrid_but_never_a_job_whose_workplace_is_unknown(drops):
    """The filter only ever drops an *explicit* signal. Everything else fails open."""
    assert drops(Item(workplace="hybrid"), switched("remote"))
    assert not drops(Item(workplace=None), switched("remote"))


def test_two_switches_on_keep_a_job_that_is_either_and_drop_the_third(drops):
    """Switches are an "or": hybrid and on-site on is a job that is hybrid or on site, never remote."""
    rules = switched("hybrid", "onsite")
    kept = {way for way in ("remote", "hybrid", "onsite") if not drops(Item(workplace=way), rules)}
    assert kept == {"hybrid", "onsite"}


def test_no_switch_on_narrows_nothing(drops):
    """Like an empty country or language list: nothing picked is nothing ruled out."""
    assert all(not drops(Item(workplace=way), switched()) for way in ("remote", "hybrid", "onsite"))


def test_a_search_saved_before_the_switches_admits_the_same_jobs(drops):
    """Its stored answer is read as the switches that admit what it did, and no answer was remote or
    hybrid. Nobody's feed changes on the day the question did."""
    def kept(section: dict) -> set[str]:
        rules = filters(sections=[{"keywords": ["backend engineer"], **section}])
        return {way for way in ("remote", "hybrid", "onsite") if not drops(Item(workplace=way), rules)}

    assert kept({"remote_policy": "remote_only"}) == {"remote"}
    assert kept({"remote_policy": "remote_or_hybrid"}) == {"remote", "hybrid"}
    assert kept({"remote_policy": "any"}) == {"remote", "hybrid", "onsite"}
    assert kept({}) == {"remote", "hybrid"}


def test_each_sections_switches_add_up_rather_than_narrow_each_other(drops):
    rules = filters(sections=[{"platform": "linkedin", "keywords": ["go"], "workplaces": ["remote"]},
                              {"platform": "all", "keywords": ["rust"], "workplaces": ["onsite"]}])
    assert not drops(Item(workplace="onsite"), rules)
    assert drops(Item(workplace="hybrid"), rules)


def test_a_job_site_s_own_words_for_a_way_of_working_are_read_as_it(db):
    """Boards name them in their own words and languages. A job titled in Spanish as hybrid is hybrid,
    and a remote one remote, for someone who switched on only on-site work — and a word that also
    names something else ("Front Office") is not read as one. Read once, when the posting's text is
    stored, and asked of the database after that: the reading at list time was a second one."""
    from libs.corpus.repository import CorpusRepository

    def dropped(title: str, location: str, rules: UserFilters) -> bool:
        corpus = CorpusRepository(db)
        job_id = corpus.add(url=f"https://example.com/{uuid.uuid4()}", source="linkedin", title=title,
                            location=location)
        corpus.set_description(job_id, "We are hiring for this role and would like to hear from you. " * 5)
        return not db[CorpusJob.COLLECTION].count_documents(
            {"$and": [{"_id": job_id}, *candidate_conditions(rules)]})

    rules = switched("onsite")
    assert dropped(".NET Developer – Madrid (Híbrido)", "Spain", rules)  # noqa: RUF001
    assert dropped("Backend Engineer (100% remoto)", "Spain", rules)
    assert dropped("Android Developer (praca zdalna)", "Wrocław", rules)
    assert not dropped("Front Office Supervisor", "Sligo, Ireland", switched("remote"))
    assert not dropped("Front Office Supervisor", "Sligo, Ireland", rules)


def test_a_linkedin_listing_at_the_display_cap_is_dropped_as_stale(drops):
    assert drops(Item(applicants=200))


def test_the_same_count_on_another_source_is_not(drops):
    """Only LinkedIn reports a count worth trusting."""
    assert not drops(Item(source="greenhouse", applicants=200))


def test_a_user_who_disabled_the_cap_sees_them(drops):
    assert not drops(Item(applicants=500), filters(applicant_cap=0))


def test_applicant_glut_needs_a_count_to_act_on(drops):
    assert not drops(Item(applicants=None))


# ── the age window the user asked for ─────────────────────────────────────────────────────────

def test_the_age_window_is_enforced_in_the_one_question_every_match_asks(drops):
    """The harvester fetches at the widest window any account asked for."""
    tight = filters(max_age_hours=6)
    assert tight.max_age_hours == 6
    assert drops(Item(posted_at=NOW - timedelta(hours=300)), tight)


def test_a_posting_inside_the_window_is_kept(drops):
    fresh = Item(posted_at=NOW - timedelta(hours=3))
    assert not drops(fresh, filters(max_age_hours=24))


def test_a_posting_with_no_dates_at_all_is_kept(drops):
    """No date is judged on when it was found, and this one was found an hour ago."""
    item = Item(posted_at=None)
    assert not drops(item, filters(max_age_hours=6))


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

def test_an_excluded_keyword_is_a_hard_no_before_scoring(drops):
    """They were honoured only by the cold-start keyword scorer."""
    item = Item(title="Contract Backend Engineer")
    assert drops(item, filters(sections=[{"exclude_keywords": ["contract"]}]))


def test_an_excluded_keyword_matches_the_company_too(drops):
    item = Item(title="Backend Engineer", company="Hays Recruitment")
    assert drops(item, filters(sections=[{"exclude_keywords": ["hays"]}]))


def test_an_excluded_term_in_the_description_does_not_drop_the_job(drops):
    """A posting that merely mentions the term in a benefits paragraph is not the job being excluded."""
    item = Item(title="Backend Engineer",
                description="We work with contract staff occasionally. " * 10)
    assert not drops(item, filters(sections=[{"exclude_keywords": ["contract"]}]))


def test_no_excluded_keywords_drops_nothing(drops):
    assert not drops(Item(), filters(sections=[{"exclude_keywords": []}]))


# ── dating the feed before filtering it ───────────────────────────────────────────────────────

MONTH_OLD = ('<span class="posted-time-ago__text"> Reposted 1 month ago </span>'
             '<span class="num-applicants__caption"> 180 applicants </span>' + "x" * 300)


def test_dating_an_undated_linkedin_job_is_what_makes_the_age_window_answerable():
    """The leak that kept "Reposted 1 month ago" in the feed."""
    from libs.corpus import postings

    dated = postings.parse_posted_at(MONTH_OLD, now=NOW)
    assert dated is not None, "an undated row stays undated and the window has nothing to test"
    assert dated < NOW - timedelta(days=20)

