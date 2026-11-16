"""The CV bench's own measurements.

These decide whether a prompt change ships, so a wrong count here is worse than no count: it would
retire a wording that was working, or adopt one that was not. `CLAUDE.md` §3a and §4.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from types import SimpleNamespace

import pytest

from scripts.cvlab import checkers, cvchecks

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


def test_what_a_cv_says_twice_in_the_same_words_is_counted():
    """Reviewers kept finding the profile repeat its first bullet word for word, and the skills list the
    duties over again: about 13 runs of three words said twice in every CV of every wording (2026-11-15).
    Counted as those runs: the same three words in a row in two of the profile, the skills and the bullets."""
    profile = ('<p class="profile">Senior chef de partie. Cut food cost from 34% to 29% in the first year.'
               "</p>")
    skills = "<li><strong>Kitchen:</strong> fresh pasta, stock ordering</li>"
    said_once = _jobs(("2021 - Present", ["Brought the food bill down by five points with the head chef",
                                          "Order fish, meat and vegetables for the week"]))
    said_twice = _jobs(("2021 - Present", ["Cut food cost from 34% to 29% in the first year with the chef",
                                           "Do the stock ordering for the week"]))

    def page(jobs: str) -> str:
        return jobs.replace("<body>", f"<body>{profile}").replace(
            "<li><strong>Databases:</strong> PostgreSQL, Redis</li>", skills)

    assert cvchecks.check(page(said_once))["said_twice"] == 0
    # "cut food cost from 34% to 29% in the first year" is ten words, eight runs of three; "the stock
    # ordering" in a skills line and a bullet is one more.
    assert cvchecks.check(page(said_twice))["said_twice"] == 9


def test_a_reader_is_given_the_words_and_not_their_markup():
    """A reviewer was shown "Fennel &amp; Flint" and "Learning &amp; Development Partner" and said so in its
    review, twice (2026-11-15): the page says "&", and so does the PDF."""
    page = '<body><p class="profile">Chef at Fennel &amp; Flint. Caf&eacute; work &lt;2 years.</p></body>'

    assert cvchecks.text_of(page) == "Chef at Fennel & Flint. Café work <2 years."
    assert cvchecks.body_parts(page + "<li>Ran R&amp;D for two years</li>") == (
        ["Ran R&D for two years"], "Chef at Fennel & Flint. Café work <2 years.")


def _jobs(*jobs: tuple[str, list[str]]) -> str:
    entries = "".join(f'<div class="entry"><div class="entry-head"><span class="entry-title">Manager</span>'
                      f'<span class="entry-date">{dates}</span></div><ul>'
                      + "".join(f"<li>{bullet}</li>" for bullet in bullets) + "</ul></div>"
                      for dates, bullets in jobs)
    return ('<html><body><div class="section competencies"><div class="section-title">Skills</div><ul>'
            "<li><strong>Databases:</strong> PostgreSQL, Redis</li></ul></div>"
            f'<div class="section"><div class="section-title">Experience</div>{entries}</div></body></html>')


def test_a_job_written_in_the_third_person_or_the_wrong_tense_is_counted():
    """Three of twelve live CVs wrote a job the person still holds as "Leads", "Manages" (2026-11-14):
    found by reading them, and counted on every run since."""
    held = ("Mar 2021 - Present", ["Leads a team of six engineers on the billing platform",
                                   "Manages the weekly release for forty shops",
                                   "Built the stock report every regional manager now reads",
                                   "Run the on-call rota for the whole platform"])
    ended = ("2018 - 2021", ["Manage the stockroom and its two daily deliveries",
                             "Cut picking errors from nine a week to two"])

    assert cvchecks.voice(_jobs(held, ended)) == {"third_person": 2, "profile_third_person": 0,
                                                  "present_in_ended": 1, "past_in_held": 1}


def test_a_profile_that_goes_on_in_the_third_person_is_counted():
    """Six of thirteen CVs reviewed on 2026-11-14 opened "Senior chef de partie…" and went on "Runs the
    pass two days a week", over bullets that say "Run"."""
    profile = ('<p class="profile">Senior chef de partie in a 70-seat restaurant. Runs the pass two days a '
               "week when the sous chef is off. Holds Level 3 food hygiene. Train the commis chefs.</p>")
    page = _jobs(("2021 - Present", ["Run the pass two days a week"])).replace("<body>", f"<body>{profile}")

    assert cvchecks.voice(page)["profile_third_person"] == 2


def test_a_bullet_that_only_describes_a_task_is_counted_in_the_bench():
    """The docgen worker no longer says these back (owner, 2026-11-14), so the bench is where a wording
    that writes more of them shows."""
    page = _jobs(("2021 - Present", ["Helped the sous chef with orders and the monthly stock take",
                                     "Cut food cost from 34% to 29% with the head chef",
                                     "Responsible for the pass on two days a week"]))

    assert cvchecks.check(page)["task_bullets"] == 2


def test_a_cv_in_its_own_voice_and_tense_counts_nothing():
    """Words that end like the third person or the past and are neither, a word the same in every tense,
    and the skills list, whose lines start with nouns."""
    held = ("2021 - Present", ["Process forty invoices a day for six client companies",
                               "Focus the weekly review on the three slowest suppliers",
                               "Set the rota for a team of twelve", "Exceed the monthly target by a tenth"])
    ended = ("2018 - 2021", ["Led a shift of fourteen pickers and loaders",
                             "Set up the returns desk that handles 900 parcels a week"])

    assert set(cvchecks.voice(_jobs(held, ended)).values()) == {0}


def test_a_cv_that_is_not_in_english_has_no_voice_count_rather_than_a_clean_one():
    german = _jobs(("04/2021 - heute", ["Elektroinstallation in Wohn- und Gewerbebauten nach den Normen",
                                        "Messungen und Prüfprotokolle für alle Anlagen im Betrieb erstellt"]),
                   ("07/2019 - 03/2021", ["Wartung und Reparatur in Mietwohnungen und Fehlersuche vor Ort"]))

    assert set(cvchecks.voice(german).values()) == {None}


def test_a_surviving_placeholder_dominates_the_violation_score():
    """A CV carrying `<<NAME>>` is not a slightly worse CV, it is one that must never be sent."""
    clean = cvchecks.check(CV)
    leaked = cvchecks.check(CV.replace("Alice Example", "<<NAME>>"))

    assert clean["placeholder"] == 0 and leaked["placeholder"] == 1
    assert cvchecks.violations(leaked) > cvchecks.violations(clean) + 50


def test_the_bench_reads_the_prompt_that_is_actually_deployed():
    """`live` must be the file every CV and letter is written with, or the baseline drifts from what users
    get."""
    from scripts.cvlab.run import LIVE_PROMPTS, load_variant
    from services.docgen.build import PROMPTS

    for kind, name in PROMPTS.items():
        assert (pathlib.Path("libs/prompts") / name).resolve() == LIVE_PROMPTS[kind]
        assert load_variant("live", kind) == LIVE_PROMPTS[kind].read_text(encoding="utf-8")
    assert load_variant("uploaded") is None, "their own document is measured without a prompt"


def test_every_variant_fills_and_asks_for_its_answer_where_it_is_read():
    """A variant with a place nothing fills, or that asks for the CV anywhere but `<cv>`, measures a prompt
    production could not run."""
    from scripts.cvlab.run import VARIANTS, Candidate, Posting, load_variant, request_for

    candidate = Candidate.__new__(Candidate)
    candidate.user = type("U", (), {"full_name": "Alice Example", "links": []})()
    candidate.documents, candidate.languages = {"cv": ["Alice Example\nEXPERIENCE\nEngineer"]}, ()
    posting = Posting({"slug": "s", "group": "g", "candidate": "c", "title": "Engineer",
                       "description": "Build things."})
    for kind, answer in (("cv", "cv"), ("cover", "letter")):
        for path in sorted(VARIANTS[kind].glob("*.md")):
            text = load_variant(path.stem, kind)
            assert f"between <{answer}> and </{answer}>" in text, f"{path.name} does not ask for <{answer}>"
            assert "{{" not in request_for(candidate, posting, text, kind).prompt


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


REVIEW = {"dimensions": {
    "six_second_pull": {"score": 8, "evidence": "I rebuilt a 500-component assembly"},
    "human_voice": {"score": 4, "evidence": "Cut cycle time from eight hours to ninety minutes"},
    "defensibility": {"score": 2, "evidence": "Led a team of forty engineers"}},
    "not_theirs": [{"line": "leveraging a robust template system", "why": "their documents do not say it"},
                   {"line": "Chartered Engineer", "why": "not in their documents"}],
    "left_out": ["SolidWorks certification"], "would_reply": "maybe", "one_line": "Thin.", "top_fixes": ["x"]}


def test_a_reviewers_mark_counts_only_when_its_quoted_line_is_in_the_cv():
    """Rules before AI: the prompt says a mark whose line is not found is thrown away, and code does it.
    The overall mark is worked out here, as the mean of the marks kept."""
    from scripts.cvlab.run import read_verdict

    verdict = read_verdict(f"Here it is:\n{json.dumps(REVIEW)}", cvchecks.text_of(CV))

    assert set(verdict["dimensions"]) == {"six_second_pull", "human_voice"} and verdict["unquoted"] == 1
    assert verdict["overall_100"] == 60 and verdict["verdict"] == "maybe"
    assert [each["line"] for each in verdict["not_theirs"]] == ["leveraging a robust template system"]
    assert verdict["left_out"] == ["SolidWorks certification"]
    assert read_verdict("No JSON here.", "a CV") is None


def test_the_reviewer_is_given_the_persons_documents_and_the_posting():
    """A reviewer that never saw their documents could not say what a CV made up: a skill and a duty planted
    in four CVs cost 0.5 of its honesty mark out of 10, and 4.6 once it was given them (2026-11-14)."""
    from scripts.cvlab import run as bench

    asked = []

    class Client:
        def complete_text(self, **call):
            asked.append(call)
            return json.dumps(REVIEW)

    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "x",
                             "description": "We need a welder."})
    verdict = bench.judge_one(Client(), posting, cvchecks.text_of(CV), {"pages": 1},
                              "MIG and TIG welding at Harland Yard, 2019 to 2024.")

    prompt = asked[0]["messages"][0]["content"]
    assert "MIG and TIG welding at Harland Yard" in prompt and "We need a welder." in prompt
    assert "I rebuilt a 500-component assembly" in prompt and '"pages": 1' in prompt
    assert not re.search(r"<(?:THEIR_DOCUMENTS|JOB_DESCRIPTION|CV_TEXT|DETERMINISTIC_SUMMARY)>", prompt)
    assert asked[0]["model"] == bench.JUDGE_MODEL and verdict["overall_100"] == 60


def test_a_linked_account_writes_one_document_at_a_time():
    """A sign-in's tool renews its session when it starts. Two at once can each renew it, and the one that
    loses retires the sign-in: the docgen worker writes one at a time for that reason, and so does the bench
    on a person's account. A key, or a run that calls nothing, goes as wide as asked."""
    from scripts.cvlab.run import at_once

    assert at_once(SimpleNamespace(account="someone@example.com", dry_run=False, concurrency=4)) == 1
    assert at_once(SimpleNamespace(account=None, dry_run=False, concurrency=4)) == 4
    assert at_once(SimpleNamespace(account="someone@example.com", dry_run=True, concurrency=4)) == 4
    # CVs already on disk, read by the reviewer on that account: nothing is written and the tool still runs.
    assert at_once(SimpleNamespace(account="someone@example.com", dry_run=True, judge=True,
                                   concurrency=4)) == 1


@pytest.mark.skipif(not os.path.exists(os.environ.get("CHROME_BIN", "")),
                    reason="needs CHROME_BIN: a real Chrome renders the PDF")
def test_the_ats_checks_say_which_a_cv_passes():
    """Each ATS check is a yes or a no that does not depend on what the person has done (owner,
    2026-11-15). A CV with the posting's title in it and its dates in one form passes them all; one that
    copies "2019-21" as the person wrote it, and never names the job, fails those two and no other."""
    from scripts.cvlab.run import Candidate, Posting, ats_checks, ats_read, rendered, request_for
    from services.docgen import build

    candidate = Candidate.__new__(Candidate)
    candidate.user = type("U", (), {"full_name": "Maya Example", "links": []})()
    candidate.documents = {"cv": ["Maya Example\nmaya@example.com | 07700 900123 | Leeds\n\nEXPERIENCE\n"
                                  "Sales assistant, Larkspur Home, Leeds, 2019-21"]}
    candidate.languages = ()
    posting = Posting({"slug": "s", "group": "g", "candidate": "c", "description": "Advise customers.",
                       "title": "Retail Advisor, 38hrs 12M FTC, Aintree"})

    def checks(dates: str, profile: str) -> dict:
        body = (f'<p class="profile">{profile}</p><div class="section"><div class="section-title">Experience'
                '</div><div class="entry"><div class="entry-head"><span class="entry-title">Sales assistant '
                f'· Larkspur Home · Leeds</span> <span class="entry-date">{dates}</span></div><ul><li>Cash '
                'up the tills at the end of the day, about three thousand pounds.</li><li>Open and close the '
                'store on Sundays when the supervisor is not in.</li><li>Help customers choose candles and '
                'home fragrance, and deal with returns, refunds and complaints.</li><li>Trained five new '
                'starters on the tills and in the stockroom.</li></ul></div></div><div class="section"><div '
                'class="section-title">Education</div><div class="entry"><div class="entry-head"><span '
                'class="entry-title">GCSEs · Hall Green School</span> <span class="entry-date">2018</span>'
                "</div></div></div>")
        page = build.assemble(request_for(candidate, posting, None), body)
        document, report = rendered(page, "cv")
        return ats_checks(document, page, posting.title, report, ats_read(document, page))

    good = checks("Sep 2022 \u2013 Present", "Sales assistant looking for a Retail Advisor role.")
    assert all(good.values()), good

    loose = checks("2019-21", "Sales assistant with four years in retail.")
    assert [name for name, ok in loose.items() if not ok] == ["dates", "title"]
    # A title is the same title with "and" for its "&", and whatever stands between its words.
    posting.title = "Retail & Sales Advisor"
    assert checks("2022", "Sales assistant. Looking for a Retail and Sales Advisor role.")["title"]


def test_a_document_that_took_more_than_a_keys_ceiling_is_counted():
    """A sign-in's tool lets the model think past the ceiling an API key is given, so the bench wrote whole
    CVs that a key would have cut off: 3 of 18 with one candidate wording. The scoreboard says how many."""
    from scripts.cvlab.run import DIMENSIONS, over_ceiling, scoreboard
    from services.docgen import build

    assert over_ceiling(build.MAX_TOKENS + 1) == 1 and over_ceiling(build.MAX_TOKENS) == 0
    assert over_ceiling(None) is None, "a tool that does not count is not a document under the ceiling"
    rows = [{"candidate": "x", "variant": variant, "job": "j", "group": "g", "sample": 1, "overall_100": 50,
             "tokens_out": tokens, "over_ceiling": over_ceiling(tokens),
             "dimensions": dict.fromkeys(DIMENSIONS, 5)}
            for variant, tokens in (("live", 9_000), ("wordier", build.MAX_TOKENS + 2_000))]

    board = scoreboard(rows)

    assert "over a key's ceiling" in board and "| over_ceiling | +1.00" in board


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


# ── tools that are not ours (`checkers.py`) ─────────────────────────────────────────────────────

# A letter as `build.assemble` lays it out: the header, the company block, the subject, then the letter.
LETTER_PAGE = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<title>Maya Example, cover letter</title>\n'
               '</head>\n<body>\n<div class="cv-header">\n<div class="cv-name">Maya Example</div>\n'
               '<div class="cv-contact"><span>Leeds, UK</span></div>\n</div>\n<hr class="header-rule">\n'
               '<div class="letter-meta">\n<p class="letter-to">Acme<br>Leeds</p>\n</div>\n'
               '<p class="letter-subject">Data analyst specialising in operations</p>\n<div class="letter">\n'
               '<p>Dear Hiring Manager,</p>\n<p>First <strong>paragraph</strong> here.</p>\n'
               '<p>Second one.</p>\n<p class="closing">Kind regards,</p>\n'
               '<p class="signature">Maya Example</p>\n</div>\n</body>\n</html>\n')

# ResyMatch's answer for a made-up CV, 2026-11-14, cut to what is read: its reader ran the phone number into
# the email address, which sat beside it on the header line with only a gap between them.
RESYMATCH_ANSWER = {
    "match_rate": "41", "ats_best_practices": "78", "hard_skills_match": "62", "soft_skills_match": "0",
    "sales_index": "25",
    "ats_data": [{"meta_key": "emails", "meta_value": "900123maya.example@example.com"},
                 {"meta_key": "phoneNumbers", "meta_value": "+44 7700 900123"}],
    "hard_data": [{"meta_key": "Reconcile", "resume_count": 0}, {"meta_key": "Tableau", "resume_count": 0},
                  {"meta_key": "Power BI", "resume_count": 1}],
    "soft_data": [{"meta_key": "Communication", "resume_count": 0}]}

# The cover letter checker's result panel for one of our letters, 2026-11-14, as the page shows it.
LETTER_SHOWN = ("71\n/ 100 cover letter strength score\nLength: 263 words \u00b7 Ideal range (200-450)\n"
                "Structure\n\u2713 Greeting (Dear / Hello)\n\u2717 Substantive opening paragraph\n"
                "\u2717 Paragraph count: 8 (ideal: 3-5)\n\u2717 Closing line (Sincerely / Best regards)\n"
                "Weak/clich\u00e9d phrases: None detected \u2713\n"
                "Quantification: Numbers found \u2713 (100, 1)\n"
                "Pronoun balance: 100% \"I/my\" \u00b7 0% \"you/your\" \u2014 too \"I\"-heavy.\n"
                "Job description keyword match: 60% Strong\n\nMatched: systems, model\n\n"
                "Missing (consider adding): product, high, inference\n\nAvg sentence length: 18.8 words\n")


def _document(tmp_path, kind="cv", **fields):
    return checkers.Document(**{"kind": kind, "path": tmp_path / "maya__live__job__1.html", "text": "",
                                "pdf": None, "title": "Analyst", "posting": "We need an analyst.",
                                "theirs": "", **fields})


def test_a_letter_is_pasted_as_a_person_would_paste_it():
    """Greeting first, a blank line between paragraphs, the sign-off and the name together, no subject.
    The letter checker counts paragraphs by blank lines and wants the last one to sign off, so a letter
    pasted any other way would score our pasting rather than the letter."""
    from scripts.cvlab.run import letter_text

    assert letter_text(LETTER_PAGE) == ("Dear Hiring Manager,\n\nFirst paragraph here.\n\nSecond one.\n\n"
                                        "Kind regards,\nMaya Example")


def test_the_header_contact_is_read_back_as_it_was_written():
    """What a checker read is compared with what the header says, so the header is read by its builder's
    own markup."""
    from scripts.cvlab.run import contact_of
    from services.docgen.build import _header

    facts = SimpleNamespace(name="Maya Example", links=(), location="Leeds, UK", phone="+44 7700 900123",
                            email="maya.example@example.com")
    _, header = _header(facts, {"full_name": "Maya Example", "links": []})

    assert contact_of(header) == ("maya.example@example.com", "+44 7700 900123")


def test_a_cv_whose_email_came_back_wrong_was_not_read(tmp_path):
    """A recruiter writes to the address the ATS read. One run into the phone number reaches nobody."""
    document = _document(tmp_path, email="maya.example@example.com", phone="+44 7700 900123")
    read_right = {**RESYMATCH_ANSWER, "ats_data": [
        {"meta_key": "emails", "meta_value": "maya.example@example.com"},
        {"meta_key": "phoneNumbers", "meta_value": "+44 7700 900123"}]}

    assert checkers.read_resymatch(RESYMATCH_ANSWER, document)["scores"]["contact_read"] == 0.0
    assert checkers.read_resymatch(read_right, document)["scores"]["contact_read"] == 1.0


def test_a_skill_left_out_counts_only_when_it_is_theirs(tmp_path):
    """The posting's skill missing from the CV is the writer's miss when their documents show it, and the
    right call when they never had it. "Reconcile" is theirs as "reconciled"; Tableau is not theirs."""
    document = _document(tmp_path, theirs="Reconciled takings each week in Excel. Built billing reports.")
    answer = checkers.read_resymatch(RESYMATCH_ANSWER, document)

    assert answer["scores"]["match"] == 41.0 and answer["scores"]["hard"] == 62.0
    assert answer["left_out"] == ["Reconcile"]
    assert answer["scores"]["left_out"] == 1


def test_what_a_resume_parser_read_is_compared_with_what_the_cv_says(tmp_path):
    """Affinda gives no score: it says what it read. Each job and qualification counts as read when the
    parser has one with its title and its employer, and its dates when that one starts in the same year
    and is still held or not, as the CV says."""
    entries = (("Staff Nurse", "St Aoife's University Hospital", "September 2022 – present"),  # noqa: RUF001
               ("Staff Nurse", "Carrowmore General Hospital", "October 2020 - August 2022"),
               ("BSc (Hons) General Nursing, 2.1", "University College Dublin", "2016 - 2020"))
    document = _document(tmp_path, name="Sinéad Kavanagh", email="sinead@example.com", phone="020 915 0143",
                         entries=entries)
    data = {"name": {"raw": "Sinéad Kavanagh"}, "emails": ["sinead@example.com"],
            "phoneNumbers": ["020 915 0143"], "skills": [{"name": "Wound Care"}],
            "workExperience": [
                {"jobTitle": "Staff Nurse", "organization": "St Aoife's University Hospital",
                 "dates": {"startDate": "2022-09-01", "isCurrent": True}},
                {"jobTitle": "Staff Nurse", "organization": "Carrowmore Hospital Group",
                 "dates": {"startDate": "2020-10-01", "isCurrent": False}}],
            "education": [{"organization": "(2.1): University College Dublin",
                           "accreditation": {"inputStr": "BSc (Hons) General Nursing,"},
                           "dates": {"startDate": "2017-01-01", "isCurrent": False}}]}

    scores = checkers.read_affinda(data, document)["scores"]

    assert scores["name_read"] == scores["contact_read"] == 1.0
    # The second job's employer came back as another name; the degree was read, with the wrong first year.
    assert scores["entries_read"] == pytest.approx(2 / 3) and scores["dates_read"] == 0.5
    cut = checkers.read_affinda({**data, "phoneNumbers": ["020 915"]}, document)["scores"]
    assert cut["contact_read"] == 0.0
    # A parser tidies what it reads, and a year written short is the year.
    tidied = {**data, "workExperience": [{"jobTitle": "Senior Chef", "organization": "St. Brendans Tearooms",
                                          "dates": {"startDate": "2023-03-01", "isCurrent": True}},
                                         {"jobTitle": "Saturday job", "organization": "Beans & Greens cafe",
                                          "dates": {"startDate": "2026-02-01", "isCurrent": False}}]}
    chef = _document(tmp_path, entries=(("Senior chef", "St Brendan's Tea Rooms", "Since March 23"),
                                        ("Saturday job", "Beans and Greens Cafe", "February – June 2026")))  # noqa: RUF001
    assert checkers.read_affinda(tidied, chef)["scores"] == pytest.approx(
        {"jobs": 2.0, "skills": 1.0, "entries_read": 1.0, "dated_entries_read": 1.0, "dates_read": 1.0})
    # A job the person gave no dates for is not read as a job of its own: that is counted among all the
    # entries and left out of the ones the writer answers for.
    undated = _document(tmp_path, entries=(*chef.entries, ("Kitchen porter", "Agency work", "")))
    assert checkers.read_affinda(tidied, undated)["scores"] == pytest.approx(
        {"jobs": 2.0, "skills": 1.0, "entries_read": 2 / 3, "dated_entries_read": 1.0, "dates_read": 1.0})
    # A number read with its country's code is the number.
    abroad = checkers.read_affinda({**data, "phoneNumbers": ["+353209150143"]}, document)["scores"]
    assert abroad["contact_read"] == 1.0


def test_a_written_cvs_jobs_are_listed_as_it_gives_them():
    from scripts.cvlab.run import entries_of

    assert entries_of(_jobs(("Mar 2021 - Present", ["Lead a team"]))) == ()  # a title with no employer
    page = ('<div class="entry"><div class="entry-head"><span class="entry-title">Staff Nurse · St '
            'Aoife&#x27;s Hospital · Dublin</span><span class="entry-date">2022 - present</span></div></div>'
            '<div class="entry"><div class="entry-head"><span class="entry-title">Technikum · Radom</span>'
            "</div>")
    assert entries_of(page) == (("Staff Nurse", "St Aoife's Hospital", "2022 - present"),
                                ("Technikum", "Radom", ""))


def test_resymatchs_answer_is_read_whether_its_rows_come_sorted_or_in_one_list(tmp_path):
    """Until 2026-11-15 the results page asked for the scan and its rows came a list a kind. Since then
    the scan button answers, with every row in one list sent as text: read as before, every score came
    back with no contact details and no skills behind it."""
    rows = [{"category": "Ats", "meta_key": "emails", "meta_value": "maya@x.com"},
            {"category": "Ats", "meta_key": "phoneNumbers", "meta_value": "+44 7700 900123"},
            {"category": "Hard Skill", "meta_key": "Excel", "resume_count": 0},
            {"category": "Hard Skill", "meta_key": "SQL", "resume_count": 2},
            {"category": "Soft Skill", "meta_key": "Kubernetes", "resume_count": 0}]
    scores = {"match_rate": 41, "ats_best_practices": 56, "hard_skills_match": 50, "soft_skills_match": 0,
              "sales_index": 25}
    document = _document(tmp_path, theirs="Excel pivot tables and SQL", email="maya@x.com",
                         phone="+44 7700 900123")
    sorted_ = {**scores, "ats_data": rows[:2], "hard_data": rows[2:4], "soft_data": rows[4:]}

    one_list = checkers.read_resymatch({**scores, "scan_results": json.dumps(rows)}, document)

    assert one_list["scores"] == checkers.read_resymatch(sorted_, document)["scores"]
    assert one_list["scores"]["contact_read"] == 1.0 and one_list["left_out"] == ["Excel"]
    assert one_list["missing"] == ["Excel", "Kubernetes"]


def test_a_checker_is_given_the_job_title_as_a_person_would_type_it():
    """A board's line carries hours, contract and place, and ResyMatch looks for the whole of what it is
    given in the CV: it found the title in 2 of 9."""
    assert checkers.job_title("Retail Advisor, 38hrs 12M FTC, Aintree") == "Retail Advisor"
    assert checkers.job_title("Office Manager - FTC 12 Months") == "Office Manager"
    # A contract said first is no part of the title either: the employer's page calls it "Staff Nurse".
    assert checkers.job_title("Bank Staff Nurse/ODP") == "Staff Nurse"
    assert checkers.job_title("Part-time Temporary Receptionist") == "Receptionist"
    assert checkers.job_title("Bank Manager") == "Bank Manager", "who manages a bank, not a contract"
    assert checkers.job_title("Warehouse Lead Operator (m/f/d)") == "Warehouse Lead Operator"
    # The same note without its brackets was cut at its first stroke: "Warehouse Lead Operator M".
    assert checkers.job_title("Warehouse Lead Operator M/F/D") == "Warehouse Lead Operator"
    assert checkers.job_title("Elektriker m/w/d - Wartung") == "Elektriker"
    whole = "Learning Facilitator & Client Consultant"
    assert checkers.job_title(whole) == whole
    assert checkers.job_title("Chef de Partie") == "Chef de Partie"


def test_the_letter_checkers_panel_is_read_as_numbers():
    """Its result is a panel of text, so a reading that silently missed a number would average a hole."""
    answer = checkers.read_letter(LETTER_SHOWN)

    assert answer["scores"] == {"score": 71.0, "words": 263.0, "paragraphs": 8.0, "i_share": 100.0,
                                "keywords": 60.0, "sentence_words": 18.8, "stock_phrases": 0.0}
    assert answer["failed"] == ["Substantive opening paragraph", "Paragraph count: 8 (ideal: 3-5)",
                                "Closing line (Sincerely / Best regards)"]
    assert answer["missing"] == ["product", "high", "inference"]


def test_a_kept_answer_is_used_and_the_site_is_not_asked_again(tmp_path, monkeypatch):
    """The tools are free and limited: asking again about an unchanged document spends a scan for nothing."""
    (tmp_path / "maya__live__job__1.html.resymatch.json").write_text(json.dumps({"scores": {"match": 41.0}}))

    def asked(_):
        raise AssertionError("the site was asked again")

    monkeypatch.setitem(checkers.CHECKERS, "resymatch", (asked, "cv"))

    assert checkers.measure(["resymatch"], _document(tmp_path)) == {"resymatch.match": 41.0}


def test_a_checker_is_only_asked_about_its_own_kind_of_document(tmp_path, monkeypatch):
    def asked(_):
        raise AssertionError("the letter checker was asked about a CV")

    monkeypatch.setitem(checkers.CHECKERS, "letter", (asked, "cover"))

    assert checkers.measure(["letter"], _document(tmp_path)) == {}


def test_a_site_that_fails_leaves_the_row_without_it_and_says_so(tmp_path, monkeypatch, caplog):
    """A site down is that row without its numbers, logged; never a failed grid, and never kept as an
    answer that would stop it being asked next time. And never logged with what was typed into it:
    Playwright's message quotes it, and it is their CV (2026-11-14)."""
    def down(_):
        raise TimeoutError("Page.fill: Timeout 30000ms exceeded.\n"
                           'Call log:\n  - fill("Maya Example\nmaya@x.com")')

    monkeypatch.setitem(checkers.CHECKERS, "resymatch", (down, "cv"))
    with caplog.at_level(logging.WARNING, logger="cvlab"):
        assert checkers.measure(["resymatch"], _document(tmp_path)) == {}

    assert "checker resymatch failed" in caplog.text and "Timeout 30000ms exceeded" in caplog.text
    assert "Maya Example" not in caplog.text and "maya@x.com" not in caplog.text
    assert not list(tmp_path.glob("*.json"))


def test_a_checkers_page_waits_as_long_as_the_bench_says_and_no_less(monkeypatch):
    """One clock. The browser's own 30 seconds stood beside the bench's two minutes: a scanner page that
    took 31 seconds to answer failed every upload at "Timeout 30000ms" while the bench was still willing to
    wait (2026-11-15, 14 scans lost)."""
    class Page:
        waits = None

        def set_default_timeout(self, milliseconds):
            self.waits = milliseconds

    monkeypatch.setattr(checkers, "_browser", lambda: SimpleNamespace(new_page=Page))

    assert checkers._page().waits == checkers.TIMEOUT_MS


def test_a_refusal_from_affinda_says_why(tmp_path, monkeypatch):
    """Affinda says why in the body ("no credits left", "file not supported"), and the status alone does
    not: one read of nine came back "400 Bad Request" and nothing told a spent trial from a bad file
    (2026-11-15)."""
    import httpx

    def refused(*args, **kwargs):
        return httpx.Response(400, json={"errors": [{"code": "no_parsing_credits"}]},
                              request=httpx.Request("POST", "https://api.eu1.affinda.com/v3/documents"))

    monkeypatch.setattr(httpx, "post", refused)
    monkeypatch.setenv("AFFINDA_API_KEY", "aff_test")
    monkeypatch.setenv("AFFINDA_COLLECTION", "abc")

    with pytest.raises(RuntimeError, match=r"400.*no_parsing_credits") as raised:
        checkers.affinda(_document(tmp_path, text="A CV"))
    assert "aff_test" not in str(raised.value)


def test_a_cover_letter_cell_is_measured_as_a_letter(tmp_path, monkeypatch):
    """A letter is pasted as a letter, its own letter is the baseline, and the CV's counts and CV judge stay
    off it. Their own letter is asked about once, whatever the sample."""
    from scripts.cvlab import run as bench

    candidate = bench.Candidate.__new__(bench.Candidate)
    candidate.name, candidate.languages = "maya", ()
    candidate.user = SimpleNamespace(full_name="Maya Example", links=[])
    candidate.documents = {"cv": ["Maya Example\nEXPERIENCE\nAnalyst"],
                           "cover_letter": ["Dear team, my own."]}
    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Analyst",
                             "company": "Acme", "description": "We need an analyst."})
    wording = "Write the letter. {{job}} <letter></letter>"
    args = SimpleNamespace(kind="cover", dry_run=True, force=False, judge=True, checkers=["letter"],
                           model="claude-haiku-5-5", effort="medium")
    # Kept by the model that wrote it and the version of the wording it was written from (owner,
    # 2026-11-15), with the wording itself beside it.
    folder = tmp_path / "claude-haiku-5-5@medium" / bench.version_of(wording)
    assert bench.kept_in(tmp_path, "claude-haiku-5-5", "medium", wording) == folder
    assert (folder / "prompt.md").read_text(encoding="utf-8") == wording
    assert bench.kept_in(tmp_path, "gpt-6-luna", "medium", wording).parent.name == "gpt-6-luna"
    # Told how hard to think by the bench alone, its documents are kept apart from the ones it was not.
    monkeypatch.setattr(bench.cli, "CODEX_EFFORT", "low")
    assert bench.kept_in(tmp_path, "gpt-6-luna", "medium", wording).parent.name == "gpt-6-luna@low"
    monkeypatch.setattr(bench.cli, "CODEX_EFFORT", None)
    (folder / "maya__live__job__1.cover.html").write_text(LETTER_PAGE, encoding="utf-8")
    asked = []
    monkeypatch.setitem(checkers.CHECKERS, "letter",
                        (lambda document: asked.append(document) or {"scores": {"score": 80.0}}, "cover"))
    monkeypatch.setattr(bench, "rendered",
                        lambda page, label, kind: (None, bench.pdf.PdfReport(pages=None, chars=0)))

    row = bench._run_cell(None, candidate, posting, variant="live", sample=1, template=wording, out=tmp_path,
                          args=args)
    assert row["kind"] == "cover" and row["letter.score"] == 80.0
    assert row["prompt_version"] == bench.version_of(wording)
    assert "first_person" not in row and row["overall_100"] is None
    assert asked[-1].text.startswith("Dear Hiring Manager,\n\n")

    bench._run_cell(None, candidate, posting, variant="uploaded", sample=2, template=None, out=tmp_path,
                    args=args)
    assert asked[-1].text == "Dear team, my own."
    assert asked[-1].path == tmp_path / "uploaded" / "maya__uploaded__job__1.cover.txt"


def test_what_other_tools_said_reaches_the_comparison():
    """An outside measure that moved must be in the paired table, or a prompt is judged on ours alone."""
    from scripts.cvlab.run import scoreboard

    rows = [{"candidate": "x", "variant": variant, "job": "j", "group": "g", "sample": 1,
             "overall_100": None, "resymatch.match": match}
            for variant, match in (("live", 40.0), ("v2", 55.0))]
    board = scoreboard(rows)

    assert "What tools that are not ours said" in board
    assert "| resymatch.match | +15.00" in board
