"""The corpus repository's behaviour — the storage half of the `store.py` port."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from libs.corpus.repository import CorpusRepository
from libs.db import get
from libs.db.models import CorpusJob


@pytest.fixture()
def repo(db):
    return CorpusRepository(db), db


def _get(s, job_id: int) -> CorpusJob:
    return get(s, CorpusJob, job_id)


def _u(path: str = "") -> str:
    return f"https://boards.greenhouse.io/acme/jobs/{path or uuid.uuid4().hex}"


# ── ingest ────────────────────────────────────────────────────────────────────────────────────

def test_adding_the_same_url_twice_inserts_once(repo):
    r, s = repo
    url = _u()
    first = r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")
    second = r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")

    assert first is not None
    assert second is None, "second insert must be ignored, not raise and not duplicate"
    assert [row["_id"] for row in s[CorpusJob.COLLECTION].find({"url": url})] == [first]


def test_rediscovering_a_job_never_makes_it_look_new(repo):
    """`first_seen_at` drives recency ordering and the retention sweep."""
    r, s = repo
    url = _u()
    job_id = r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")
    original = _get(s, job_id).first_seen_at

    r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")

    assert _get(s, job_id).first_seen_at == original, "first_seen_at must never be overwritten"


# ── dedup ─────────────────────────────────────────────────────────────────────────────────────

def test_dedup_matches_on_exact_url(repo):
    r, _s = repo
    url = _u()
    r.add(url=url, source="greenhouse", title="Backend Engineer", company="Acme")

    hit = r.find_duplicate(url, "Backend Engineer", "Acme")
    assert hit is not None
    assert hit.matched_on == "url"


def test_dedup_matches_across_tracking_noise_via_canonical(repo):
    """The same posting linked with `?gh_src=` is not a second job."""
    r, _s = repo
    token = uuid.uuid4().hex
    r.add(url=_u(token), source="greenhouse", title="Backend Engineer", company="Acme")

    hit = r.find_duplicate(_u(token) + "?gh_src=abcd", "Backend Engineer", "Acme")
    assert hit is not None
    assert hit.matched_on == "canonical_url"


def test_dedup_matches_the_same_role_on_an_unrelated_board(repo):
    """LinkedIn and Greenhouse URLs share nothing, so only the content key can see this."""
    r, _s = repo
    company = f"Acme{uuid.uuid4().hex[:8]}"
    r.add(url=_u(), source="greenhouse", title="Backend Engineer", company=company,
          location="London")

    hit = r.find_duplicate("https://www.linkedin.com/jobs/view/4012345678/",
                           "Backend Engineer", company, "London")
    assert hit is not None
    assert hit.matched_on == "content_key"


def test_the_same_role_in_another_city_is_not_a_duplicate(repo):
    """Location is part of the content key on purpose — two cities are two jobs."""
    r, _s = repo
    company = f"Acme{uuid.uuid4().hex[:8]}"
    r.add(url=_u(), source="greenhouse", title="Backend Engineer", company=company,
          location="London")

    assert r.find_duplicate(_u(), "Backend Engineer", company, "Berlin") is None


def test_an_unusable_key_never_matches_another_unusable_key(repo):
    """Fail open."""
    r, _s = repo
    r.add(url="https://example.test/" + uuid.uuid4().hex, source="board", title="Engineer",
          company="")            # no company -> content_key is None

    # A different job, also with no usable content key, must not match the first.
    assert r.find_duplicate("https://example.test/" + uuid.uuid4().hex, "Engineer", "") is None


def test_a_genuinely_new_job_is_not_a_duplicate(repo):
    r, _ = repo
    assert r.find_duplicate(_u(), "Staff Platform Engineer", f"New{uuid.uuid4().hex[:8]}") is None


# ── the apply-URL layer: what canonicalisation actually exists for ────────────────────────────
# The same job arrives from the LinkedIn scrape and from the ATS poll under different URLs.

def test_a_linkedin_listing_resolving_to_a_known_ats_url_is_a_duplicate(repo):
    """LinkedIn arrives second; the Greenhouse row is already in the corpus."""
    r, _s = repo
    token = uuid.uuid4().hex
    r.add(url=_u(token), source="greenhouse", title="Backend Engineer",
          company=f"Acme{uuid.uuid4().hex[:8]}")

    hit = r.find_duplicate(
        "https://www.linkedin.com/jobs/view/4012345678/",
        title="Backend Engineer (Remote) - Growing Startup",   # decorated: content key will differ
        company="Acme Technologies International",             # abbreviated differently
        application_url=_u(token) + "?gh_src=linkedin")
    assert hit is not None
    assert hit.matched_on == "application_url"


def test_an_ats_row_arriving_after_the_linkedin_one_is_a_duplicate(repo):
    """The other direction: the ATS poll arrives second, beaten by a LinkedIn listing."""
    r, _s = repo
    token = uuid.uuid4().hex
    ats_url = _u(token)

    linkedin_id = r.add(url="https://www.linkedin.com/jobs/view/" + uuid.uuid4().hex,
                        source="linkedin", title="Backend Engineer (Remote)",
                        company="Acme Technologies International")
    r.set_application_url(linkedin_id, ats_url)

    hit = r.find_duplicate(ats_url, title="Backend Engineer",
                           company=f"Acme{uuid.uuid4().hex[:8]}")
    assert hit is not None
    assert hit.matched_on == "application_url"
    assert hit.job_id == linkedin_id


def test_the_apply_url_layer_ignores_tracking_noise(repo):
    """It compares canonical forms, so `?gh_src=linkedin` is not a different apply target."""
    r, _s = repo
    token = uuid.uuid4().hex
    job_id = r.add(url=_u(token), source="greenhouse", title="Backend Engineer",
                   company=f"Acme{uuid.uuid4().hex[:8]}")

    hit = r.find_duplicate("https://www.linkedin.com/jobs/view/9999/",
                           title="Unrelated Title", company=f"Other{uuid.uuid4().hex[:8]}",
                           application_url=_u(token) + "?utm_source=x#apply")
    assert hit is not None and hit.job_id == job_id


def test_an_unresolvable_apply_url_does_not_match_anything(repo):
    """Fail open, same contract as the other layers."""
    r, _s = repo
    r.add(url=_u(), source="greenhouse", title="Backend Engineer",
          company=f"Acme{uuid.uuid4().hex[:8]}")

    assert r.find_duplicate(_u(), "Totally Different Role",
                            f"Other{uuid.uuid4().hex[:8]}", application_url="") is None
    assert r.find_duplicate(_u(), "Totally Different Role",
                            f"Other{uuid.uuid4().hex[:8]}",
                            application_url="not-a-url") is None


# ── description ──────────────────────────────────────────────────────────────────────────────

def test_rewriting_identical_text_is_not_a_change(repo):
    """Re-fetching a posting that did not change is not news; new text is, and is stored."""
    r, s = repo
    job_id = r.add(url=_u(), source="greenhouse", title="Backend Engineer", company="Acme")
    assert r.set_description(job_id, "Go, gRPC, Postgres.") is True

    assert r.set_description(job_id, "Go, gRPC, Postgres.") is False
    assert r.set_description(job_id, "Rust, Kafka, ClickHouse.") is True
    assert _get(s, job_id).description == "Rust, Kafka, ClickHouse."


# ── retention ────────────────────────────────────────────────────────────────────────────────

def test_the_repository_no_longer_trims_rows_in_place():
    """Removed 2026-08-11, and this fails if it comes back.

    `trim_cold` nulled the description and vector and kept the row. The trainer joins labels back to
    the corpus and requires `embedding IS NOT NULL`, so a trimmed row silently left every future
    training set. Retention is `libs.corpus.archive` now: the whole row, written out then deleted.
    """
    assert not hasattr(CorpusRepository, "trim_cold")
    assert not hasattr(CorpusRepository, "pin")


def test_a_long_real_world_content_key_is_storable(repo):
    """Regression from the first real harvest cycle."""
    r, s = repo
    job_id = r.add(
        url="https://www.arbeitnow.com/jobs/companies/osnatruck-nutzfahrzeugservice-gmbh/"
            "mitarbeiter-buchhaltung-in-voll-oder-teilzeit-osnabruck-435649",
        source="arbeitnow",
        title="Mitarbeiter Buchhaltung (m/w/d) in Voll- oder Teilzeit",
        company="OsnaTruck Nutzfahrzeugservice GmbH",
        location="Osnabrück")

    job = _get(s, job_id)
    assert job.content_key and len(job.content_key) > 64


def test_a_very_long_title_does_not_break_ingest(repo):
    """No bound at all is the right answer here: any bound is a guess about someone else's data."""
    r, s = repo
    job_id = r.add(url=f"https://example.test/{uuid.uuid4().hex}", source="board",
                   title="Senior " * 60 + "Engineer", company="A" * 300, location="B" * 300)
    assert _get(s, job_id).content_key


def test_every_corpus_timestamp_comes_from_one_clock_and_it_is_the_real_one(repo):
    """The defect that made an embed appear to finish before its description arrived."""
    corpus, s = repo
    job_id = corpus.add(url=_u(), source="test", title="Engineer",
                        fetched_at=datetime.now(UTC) - timedelta(seconds=1))
    assert job_id is not None
    corpus.set_description(job_id, "A real description. " * 40)

    row = _get(s, job_id)
    # Every one of these is written by a different statement, and they must be readable in order.
    assert row.fetched_at <= row.first_seen_at <= row.description_fetched_at, (
        "a corpus row's timestamps are out of order — something is reading a clock other than the "
        "wall clock")



def test_a_posting_is_never_recorded_as_published_after_we_found_it(repo):
    """LinkedIn reports age relatively and we read it late."""
    corpus, s = repo
    job_id = corpus.add(url=_u(), source="linkedin", title="Engineer")
    seen = _get(s, job_id).first_seen_at

    # A caption read an hour after ingest, claiming the job went up "just now".
    corpus.set_posting_metadata(job_id, posted_at=seen + timedelta(hours=1))
    assert _get(s, job_id).posted_at <= seen

    # A genuinely older timestamp is kept exactly as given — the clamp is a ceiling, not a rewrite.
    older = seen - timedelta(days=2)
    corpus.set_posting_metadata(job_id, posted_at=older)
    assert _get(s, job_id).posted_at == older


def test_the_earliest_of_linkedins_readings_is_kept_and_a_date_with_no_time_gives_way(repo):
    """LinkedIn rounds a job's age down, so each "N ago" is at or after the truth and the earliest is the
    nearest: the list's "39 minutes ago", read as the job was found, over its page's "1 hour ago" read 80
    minutes on, which is 20 minutes late. A date with no time says less than either."""
    corpus, s = repo
    listed = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=39)
    exact = corpus.add(url=_u(), source="linkedin", title="Engineer", posted_at=listed)
    corpus.set_posting_metadata(exact, posted_at=listed + timedelta(minutes=80 + 39) - timedelta(hours=1))
    assert _get(s, exact).posted_at == listed

    dated = corpus.add(url=_u(), source="linkedin", title="Engineer II",
                       posted_at=listed.replace(hour=0, minute=0, second=0))
    page = _get(s, dated).first_seen_at - timedelta(hours=2)
    corpus.set_posting_metadata(dated, posted_at=page)
    assert _get(s, dated).posted_at == page


# ── the working arrangement, classified when the text arrives (ROADMAP 6aj) ───────────────────


def test_a_description_arriving_classifies_the_working_arrangement(repo):
    """The filter that was barely running."""
    repo, s = repo
    job_id = repo.add(url=f"https://example.com/{uuid.uuid4()}", source="linkedin",
                      title="Backend Engineer", company="Acme", location="Berlin")
    assert _get(s, job_id).workplace is None, "nothing to classify from yet"

    repo.set_description(job_id, "We are a fully remote team and this role is remote. " * 8)

    assert _get(s, job_id).workplace == "remote"


def test_the_page_s_own_tag_is_never_overwritten_by_the_guess(repo):
    """LinkedIn's workplace pill and schema.org's `jobLocationType` are the real tag on the page."""
    repo, s = repo
    job_id = repo.add(url=f"https://example.com/{uuid.uuid4()}", source="linkedin",
                      title="Backend Engineer", company="Acme", location="Berlin")
    repo.set_posting_metadata(job_id, workplace="onsite")

    repo.set_description(job_id, "This is a fully remote position, work from anywhere. " * 8)

    assert _get(s, job_id).workplace == "onsite"


def test_an_unreadable_description_leaves_the_arrangement_unknown(repo):
    """Unknown means keep, here as everywhere else."""
    repo, s = repo
    job_id = repo.add(url=f"https://example.com/{uuid.uuid4()}", source="test",
                      title="Backend Engineer", company="Acme", location="Berlin")

    repo.set_description(job_id, "We build things. You will build things with us. " * 8)

    assert _get(s, job_id).workplace is None


def test_a_new_row_carries_the_country_it_was_read_from(repo):
    """The column exists so the filter is an indexed equality, not a regex per user per job."""
    repo, s = repo
    job_id = repo.add(url=f"https://example.com/{uuid.uuid4()}", source="test",
                      title="Backend Engineer", company="Acme", location="Berlin, Germany")

    assert _get(s, job_id).country == "DE"


def test_a_posting_that_names_no_place_stores_no_country(repo):
    """NULL is the value that passes every filter, and 16% of the corpus has it."""
    repo, s = repo
    job_id = repo.add(url=f"https://example.com/{uuid.uuid4()}", source="test",
                      title="Backend Engineer", company="Acme", location="Remote")

    assert _get(s, job_id).country is None


def test_the_country_column_is_read_from_the_same_place_the_filter_will_be(repo):
    """One definition."""
    from libs.corpus.geo import EU_COUNTRIES, allows, wanted_countries

    repo, s = repo
    job_id = repo.add(url=f"https://example.com/{uuid.uuid4()}", source="test",
                      title="Backend Engineer", company="Acme", location="Toulouse, FR")

    country = _get(s, job_id).country
    assert country == "FR"
    assert country in EU_COUNTRIES
    assert allows(country, wanted_countries(["europe"])) is True
    assert allows(country, wanted_countries(["united_kingdom"])) is False
