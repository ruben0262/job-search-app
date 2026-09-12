"""The CV bench's own measurements.

These decide whether a prompt change ships, so a wrong count here is worse than no count: it would
retire a wording that was working, or adopt one that was not. `CLAUDE.md` §3a and §4.
"""
from __future__ import annotations

import pathlib

from scripts.cvlab import cvchecks

# The shape docgen actually produces: a style block, a profile paragraph, entries with bullets.
CV = """<!DOCTYPE html><html><head><style>.profile { color: red; } /* I, my, me */</style></head>
<body>
<div class="cv-header"><div class="cv-name">Alice Example</div></div>
<p class="profile">I rebuilt a 500-component assembly, and my work runs the full loop.</p>
<div class="section competencies"><ul>
  <li>CAD &amp; Design: SolidWorks, top-down assembly, configuration control</li>
</ul></div>
<div class="section"><div class="entry"><div class="entry-head">
  <span class="entry-title">Design Engineer &mdash; Engine reconstruction</span></div>
  <ul>
    <li>Converted a 500-part dataset into a controlled parametric model: datums, intent, structure.</li>
    <li>Cut cycle time from eight hours to ninety minutes, leveraging a robust template system.</li>
    <li>Built the piston system as a reusable subassembly &mdash; patternable across six cylinders.</li>
  </ul></div></div>
</body></html>"""


def test_first_person_is_counted_in_the_profile_and_the_bullets():
    """The measure the third-person draft is judged on."""
    report = cvchecks.check(CV)

    # "I", "my" in the profile paragraph. The style block is stripped before anything is counted,
    # so the comment inside it must not contribute.
    assert report["profile_first_person"] == 2
    assert report["first_person"] == 2


def test_an_entry_title_numeral_is_not_read_as_the_pronoun():
    """`Design Engineer I` and `I/O` are not first person, and a naive \\bI\\b says they are."""
    html = ('<p class="profile">Backend engineer.</p><div class="entry"><ul>'
            '<li>Design Engineer I on the I/O layer, shipping to production.</li></ul></div>')

    assert cvchecks.check(html)["first_person"] == 0


def test_a_competency_line_colon_is_the_format_not_prose():
    """`Group: a, b, c` is how the section is written, so counting its colon would flag every CV."""
    report = cvchecks.check(CV)

    assert report["competency_lines"] == 1
    # Only the "Converted a 500-part dataset…: datums…" bullet counts.
    assert report["colon_bullets"] == 1


def test_em_dashes_are_counted_in_bullets_and_not_in_entry_titles():
    """The live prompt bans them in the two places, and allows them in the title header."""
    report = cvchecks.check(CV)

    assert report["emdash_violations"] == 1, "the title's em-dash must not be counted"


def test_a_surviving_placeholder_dominates_the_violation_score():
    """A CV carrying `<<NAME>>` is not a slightly worse CV, it is one that must never be sent."""
    clean = cvchecks.check(CV)
    leaked = cvchecks.check(CV.replace("Alice Example", "<<NAME>>"))

    assert clean["placeholder"] == 0 and leaked["placeholder"] == 1
    assert cvchecks.violations(leaked) > cvchecks.violations(clean) + 50


def test_the_bench_reads_the_prompt_that_is_actually_deployed():
    """`live` must be the seeded file itself, or the baseline drifts from what users get."""
    from scripts.cvlab.run import LIVE_PROMPT, load_variant

    assert pathlib.Path("libs/scaffolds/tailor_cv.md").resolve() == LIVE_PROMPT
    assert load_variant("live") == LIVE_PROMPT.read_text(encoding="utf-8")


def test_every_variant_keeps_the_placeholders_python_substitutes():
    """Breaking one breaks the build, and the bench would measure a prompt nothing could run."""
    from scripts.cvlab.run import HERE, load_variant

    for path in sorted((HERE / "variants").glob("*.md")):
        text = load_variant(path.stem)
        assert "<JOB_CONTEXT>" in text, f"{path.name} lost <JOB_CONTEXT>"
        assert "<BASE_TEMPLATE>" in text, f"{path.name} lost <BASE_TEMPLATE>"
        assert "<<ROLE_ANGLES>>" in text, f"{path.name} lost <<ROLE_ANGLES>>"


def test_the_variants_differ_from_live_only_by_the_drafted_wording():
    """A variant that also drags in an unrelated edit measures two changes and attributes one."""
    from scripts.cvlab.run import LIVE_PROMPT, load_variant

    live = load_variant("live")
    person, fallback = load_variant("d-person"), load_variant("d-fallback")

    assert LIVE_PROMPT.exists()
    # Each draft adds text and removes only the line it replaces.
    assert len(person) > len(live) and len(fallback) > len(live)
    assert "no \"I\" or \"my\"" in person and "no \"I\" or \"my\"" not in live
    assert "WHEN THERE IS NOT ENOUGH DIRECTLY RELEVANT EXPERIENCE" in fallback
    assert "WHEN THERE IS NOT ENOUGH DIRECTLY RELEVANT EXPERIENCE" not in live
    # And the live prompt is untouched by all of it (§3a).
    assert "WHEN THERE IS NOT ENOUGH DIRECTLY RELEVANT EXPERIENCE" not in live


def test_the_judge_dimensions_reach_the_comparison():
    """They arrive nested, and reading them top-level reported `None` for all seven.

    The scoreboard then compared `overall_100` alone and printed nothing about the dimensions —
    a comparison that cannot say what did not move, which is half of what §3a asks for.
    """
    from scripts.cvlab.run import DIMENSIONS, scoreboard

    rows = [{"candidate": "x", "variant": variant, "job": "j", "group": "g", "sample": 1,
             "overall_100": 50, "first_person": 0, "profile_first_person": 0, "colon_bullets": 0,
             "emdash_violations": 0, "cliches": 0, "profile_words": 60, "entries": 5,
             "bullets": 10, "est_pages": 1.5,
             "dimensions": dict.fromkeys(DIMENSIONS, score)}
            for variant, score in (("live", 5), ("d-both", 8))]

    board = scoreboard(rows)
    for name in DIMENSIONS:
        assert name in board, f"{name} is missing from the scoreboard"
    # The draft minus the deployed prompt, on every dimension, whichever order the rows arrived in.
    assert "| six_second_pull | +3.00" in board
    assert scoreboard(list(reversed(rows))) == board, "the baseline must not depend on row order"


def test_the_comparison_never_calls_a_decrease_worse():
    """Half these measures improve by going down, so a `better` column would invert the finding.

    The third-person draft's entire purpose is fewer first-person references. Labelling that
    column "worse" reported the change working as the change failing.
    """
    from scripts.cvlab.run import DIMENSIONS, scoreboard

    rows = [{"candidate": "x", "variant": variant, "job": "j", "group": "g", "sample": 1,
             "overall_100": 50, "first_person": people, "profile_first_person": people,
             "colon_bullets": 0, "emdash_violations": 0, "cliches": 0, "profile_words": 60,
             "entries": 5, "bullets": 10, "est_pages": 1.5,
             "dimensions": dict.fromkeys(DIMENSIONS, 5)}
            for variant, people in (("live", 3), ("d-person", 0))]

    board = scoreboard(rows)
    assert "higher / lower / same" in board
    assert "better / worse" not in board
    assert "std error" in board, "a difference with no error bar invites shipping on noise"


def test_a_cached_verdict_is_reused_and_costs_nothing(tmp_path):
    """Every resume re-judged the finished cells before writing a new one, so an interrupted
    grid spent its whole next run re-buying judgements it already had and never progressed."""
    import json

    from scripts.cvlab.run import _verdict

    kept = tmp_path / "cell.verdict.json"
    kept.write_text(json.dumps({"overall_100": 61, "dimensions": {}}), encoding="utf-8")

    # `client=None` stands in for "no credential available": a cached verdict must not need one.
    got = _verdict(None, None, "<html></html>", {}, kept=kept, judge=True, force=False)

    assert got["overall_100"] == 61


def test_asking_for_no_judgement_returns_none_rather_than_reading_the_cache():
    """`--dry-run` without `--judge` reports the mechanical checks only, on purpose."""
    from scripts.cvlab.run import _verdict

    assert _verdict(None, None, "<html></html>", {}, kept=pathlib.Path("nope.json"),
                    judge=False, force=False) is None
