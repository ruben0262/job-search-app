"""The company list: a name becomes the boards that list jobs under it, stored for the harvest to poll,
and nothing that answers with no jobs (owner, 2026-11-02)."""
from __future__ import annotations

import importlib.util
import pathlib
import sys

from services.harvester.sources.base import JobLink
from tests.conftest import needs_db


def _seed():
    spec = importlib.util.spec_from_file_location(
        "seed_ats", pathlib.Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "seed_ats.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    return seed


def test_a_name_is_tried_written_together_and_with_hyphens_unless_it_names_its_own_board():
    seed = _seed()
    assert seed.tokens("Grafana Labs") == ["grafanalabs", "grafana-labs"]
    assert seed.tokens("Zühlke") == ["zuhlke"]
    assert seed.tokens("Scale AI = scaleai") == ["scaleai"]
    assert seed.tokens("Checkout.com = checkoutcom, checkout") == ["checkoutcom", "checkout"]


@needs_db
def test_only_boards_that_list_jobs_are_stored(db, monkeypatch, tmp_path):
    from libs.corpus.ats import AtsRepository

    seed = _seed()
    (tmp_path / "companies.txt").write_text("# a note\nAcme Corp\nNobody = nobody\n", encoding="utf-8")
    monkeypatch.setattr(seed, "COMPANIES", tmp_path / "companies.txt")
    monkeypatch.setattr(seed, "database", lambda: db)
    job = JobLink(url="https://boards.greenhouse.io/acmecorp/jobs/1", title="Engineer", company="Acme",
                  location="Berlin", source="greenhouse")
    # Greenhouse has Acme's board; every other ATS answers for any name with an empty one.
    fetch = {name: (lambda _token, *_: ([], None)) for name in seed.FETCHERS}
    fetch["greenhouse"] = lambda token, *_: ([job] if token == "acmecorp" else [], None)
    monkeypatch.setattr(seed, "FETCHERS", fetch)
    monkeypatch.setattr(sys, "argv", ["seed_ats.py"])

    seed.main()

    repo = AtsRepository(db)
    assert repo.live_tokens("greenhouse") == ["acmecorp"]
    assert not any(repo.live_tokens(name) for name in fetch if name != "greenhouse")
    assert repo.names("greenhouse") == {"acmecorp": "Acme Corp"}, "the name its jobs will carry"
