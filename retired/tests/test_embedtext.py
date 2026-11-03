"""What a job was embedded from: the tests of `libs/corpus/embedtext.py`, retired with it on 2026-11-03
(nothing live embeds since the model was switched off). Not run: `pytest tests/` is the suite."""
from __future__ import annotations


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
