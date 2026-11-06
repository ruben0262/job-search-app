"""The LinkedIn coverage check: of the jobs LinkedIn shows for a country's hour, asked three ways, how many
are stored, and which of those listed long enough ago are not (2026-11-06)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from libs.corpus.repository import CorpusRepository

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "check_linkedin_coverage.py"


def _script():
    spec = importlib.util.spec_from_file_location("check_linkedin_coverage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _card(job_id: int, minutes_ago: int) -> str:
    return (f'<li><div class="base-card base-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">'
            f'<h3 class="base-search-card__title">Job {job_id}</h3>'
            f'<h4 class="base-search-card__subtitle">Company {job_id}</h4>'
            f'<span class="job-search-card__location">Dublin, Ireland</span>'
            f'<time class="job-search-card__listdate" datetime="2026-11-06">{minutes_ago} minutes ago</time>'
            '</div></li>')


class LinkedIn:
    """Each search's list: (location, word) to (job id, minutes since it was listed)."""

    def __init__(self, lists):
        self.lists, self.asked = lists, []

    def get(self, _url, params=None, **_kwargs):
        self.asked.append(params)
        jobs = self.lists.get((params["location"], params["keywords"]), []) if params["start"] == 0 else []
        return SimpleNamespace(status_code=200, text="".join(_card(*job) for job in jobs))


def test_each_way_of_asking_is_counted_against_what_is_stored_and_the_settled_misses_named(db):
    corpus = CorpusRepository(db)
    for job_id in (1, 2):
        corpus.add(url=f"https://www.linkedin.com/jobs/view/{job_id}", source="linkedin",
                   title=f"Job {job_id}", company=f"Company {job_id}", location="Dublin, Ireland")
    site = LinkedIn({("Ireland", ""): [(1, 50), (2, 40)],
                     ("Dublin, Ireland", ""): [(2, 40), (3, 45)],
                     ("Ireland", "nurse"): [(1, 50), (4, 5)]})

    found = _script().coverage(db, site, "Ireland", minutes=60, cities=["Dublin"], words=["nurse"])

    assert list(found["ways"].values()) == [(2, 2), (2, 1), (2, 1)]
    assert found["all"] == (4, 2)
    # Job 4 was listed five minutes ago, so the run may not have swept it yet: only job 3 is a miss.
    assert found["settled"] == (3, 2)
    assert [(job.url.rsplit("/", 1)[-1], ways) for job, ways in found["missing"]] == [
        ("3", ["its cities' lists (1)"])]
    assert {(asked["location"], asked["keywords"]) for asked in site.asked} == set(site.lists), \
        "the country as the run asks it, each city, each word, for the same hour"
    windows = {asked["f_TPR"] for asked in site.asked}
    assert len(windows) == 1, "the same hour each way"
    assert 3600 < int(windows.pop()[1:]) < 3600 + 60, "and a number LinkedIn has not been asked lately"
