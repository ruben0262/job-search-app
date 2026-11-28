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

    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "x", "title": "Welder",
                             "location": "Leeds, England", "description": "We need a welder."})
    verdict = bench.judge_one(Client(), posting, cvchecks.text_of(CV), {"pages": 1},
                              "MIG and TIG welding at Harland Yard, 2019 to 2024.")

    prompt = asked[0]["messages"][0]["content"]
    assert "MIG and TIG welding at Harland Yard" in prompt and "We need a welder." in prompt
    # The posting as its writer was given it, place and all: a reviewer not shown the place took "Screwfix in
    # Leeds", "Prezzo in Chester" and three more for places a letter made up (5 of 34 lines, 2026-11-18).
    assert "Location: Leeds, England" in prompt
    assert "I rebuilt a 500-component assembly" in prompt and '"pages": 1' in prompt
    assert not re.search(r"<(?:THEIR_DOCUMENTS|JOB_DESCRIPTION|CV_TEXT|DETERMINISTIC_SUMMARY)>", prompt)
    assert asked[0]["model"] == bench.JUDGE_MODEL and verdict["overall_100"] == 60


def test_the_bench_writes_a_chatgpt_letter_lean_as_production_does():
    """2026-11-19, night: the bench handed `build.draft` its lazy client, which is no `CliClient`, so 72 Sol
    letters went to Codex plain (19,700 tokens in, not 8,800) and were kept under `+lean`. A letter on a
    ChatGPT sign-in is written lean on the bench as in production, and is kept and recorded as lean."""
    from libs.llm import cli
    from scripts.cvlab import run as bench

    told = []

    class Codex(cli.CliClient):
        def complete_text(self, **call):
            told.append(call.get("lean"))
            return ('<letter><div class="letter"><p>Dear Hiring Manager,</p><p>I ran the ward rota.</p>'
                    '<p class="closing">Kind regards,</p><p class="signature">Maya Example</p>'
                    '</div></letter>')

    client = bench._LazyClient(None, None, "codex")
    client._client = Codex("codex_login", "{}", [].append)
    candidate = bench.Candidate.__new__(bench.Candidate)
    candidate.name, candidate.languages, candidate.documents = "maya", (), {"cv": ["Maya Example\nNurse"]}
    candidate.user = SimpleNamespace(full_name="Maya Example", links=[])
    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Nurse",
                             "company": "Acme", "description": "We need a nurse."})

    bench.write_one(client, candidate, posting, template="Write it. {{job}} <letter></letter>",
                    model="gpt-6.2-sol", kind="cover")

    assert told == [True]


def test_the_bench_can_write_on_the_claude_code_of_its_own_machine_as_production_calls_a_sign_in(
        tmp_path, monkeypatch):
    """Owner, 2026-11-27: rounds "without taking up much usage from the other claude account". With
    `--own-claude` the bench's Claude is the Claude Code signed in on the machine it runs on: the tool is
    given exactly what production gives it for a person's sign-in (the same arguments, the platform's system
    prompt, no tools), under the real home with no sign-in folder of its own, in one folder kept for it. A
    ChatGPT writer is still the account's: only Claude has a login of the machine's to use."""
    import sys

    from libs.llm import cli
    from scripts.cvlab import run as bench

    tool = tmp_path / "claude"
    tool.write_text(f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
at = args.index("--system-prompt-file") + 1
said = {{"args": args[:at] + ["<system>"] + args[at + 1:], "system": open(args[at]).read(),
        "prompt": sys.stdin.read(), "home": os.environ["HOME"], "cwd": os.getcwd(), "beside": os.listdir("."),
        "own_folder": os.environ.get("CLAUDE_CONFIG_DIR")}}
print(json.dumps({{"type": "result", "is_error": False, "result": json.dumps(said)}}))
""")
    tool.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CLI", str(tool))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "environment", cli.environment)  # the bench's own is taken out again after
    monkeypatch.setattr(bench._OwnClaude, "WORK", tmp_path / "work")
    asked = {"model": "claude-haiku-5-5", "system": "Write letters plainly.", "max_tokens": 1,
             "effort": "medium", "messages": [{"role": "user", "content": "The job: a nurse."}]}

    stored = cli.CliClient("claude_login", json.dumps({".credentials.json": "{}"}), lambda renewed: None)
    as_production = json.loads(stored.complete_text(**asked))
    own = json.loads(bench._LazyClient(None, "someone@example.com", own=True).opened().complete_text(**asked))

    assert own["args"] == as_production["args"] and "--tools" in own["args"]
    assert (own["system"], own["prompt"]) == ("Write letters plainly.", "The job: a nurse.")
    assert own["home"] == str(home) and own["own_folder"] is None and as_production["own_folder"]
    assert own["cwd"] == str(tmp_path / "work") and own["beside"] == []
    assert not bench._LazyClient(None, "someone@example.com", "codex", own=True)._own


def test_the_bench_writes_no_letter_for_someone_without_one_of_their_own_as_the_site_does(tmp_path):
    """The site makes no cover letter for someone who has not uploaded one of their own
    (`libs.documents.needs`), and 19 of the letter bench's 25 people had none: a case no person can reach
    (2026-11-20). The bench refuses it as the site does. `--overlay` gives them one, and a weaker About you,
    without changing the panel, whose people the fit score was rated on."""
    from scripts.cvlab import run as bench

    panel, overlay = tmp_path / "panel", tmp_path / "letters"
    (panel / "maya").mkdir(parents=True)
    (panel / "maya" / "candidate.json").write_text(json.dumps({"name": "Maya Example"}), encoding="utf-8")
    (panel / "maya" / "cv.txt").write_text("Maya Example\nNurse", encoding="utf-8")
    (panel / "maya" / "history.txt").write_text("A ward job in Leeds, days only.", encoding="utf-8")
    args = SimpleNamespace(jobs_dir=str(tmp_path), out=str(tmp_path / "out"), candidates="maya",
                           candidates_dir=str(panel), overlay=None, kind="cover")

    with pytest.raises(SystemExit, match="no cover_letter of their own for maya"):
        bench.run(args)

    (overlay / "maya").mkdir(parents=True)
    (overlay / "maya" / "cover_letter.txt").write_text("Dear Sir, I am hard working.", encoding="utf-8")
    (overlay / "maya" / "history.txt").write_text("ward job leeds days", encoding="utf-8")

    assert bench.Candidate("maya", panel, overlay).documents == {
        "cv": ["Maya Example\nNurse"], "cover_letter": ["Dear Sir, I am hard working."],
        "history": ["ward job leeds days"]}
    assert bench.Candidate("maya", panel).documents["history"] == ["A ward job in Leeds, days only."]


def test_a_run_stops_at_its_first_document_when_it_cost_far_more_than_those_kept_for_its_setup(tmp_path):
    """2026-11-19: 72 letters were written plain and kept as lean, at twice the tokens, and spent a ChatGPT
    plan's five hours before anyone looked. The first document a run pays for is checked alone, and the run
    stops there; one that costs what the kept ones did lets the rest go ahead unchecked."""
    from scripts.cvlab import run as bench

    model = tmp_path / "gpt-6.2-sol+lean"
    for at, tokens in enumerate((8_600, 8_800, 9_100)):
        (model / "live").mkdir(parents=True, exist_ok=True)
        kept = model / "live" / f"p{at}__live__job__1.cover.cost.json"
        kept.write_text(json.dumps({"tokens_in": tokens}))
    (model / "live" / "p__live__job__1.cost.json").write_text(json.dumps({"tokens_in": 1}))  # a CV's
    new = model / "v12"
    new.mkdir()
    paid = []

    def write(tokens):
        return lambda: paid.append(tokens) or ("page", {"tokens_in": tokens})

    plain = bench._FirstPaid()
    with pytest.raises(SystemExit, match="19,700 tokens in where the 3 kept"):
        plain(write(19_700), new, ".cover", SimpleNamespace())
    with pytest.raises(SystemExit):
        plain(write(19_700), new, ".cover", SimpleNamespace())
    assert paid == [19_700]

    lean = bench._FirstPaid()
    assert lean(write(8_700), new, ".cover", SimpleNamespace()) == ("page", {"tokens_in": 8_700})
    assert lean(write(30_000), new, ".cover", SimpleNamespace())[1]["tokens_in"] == 30_000
    told = bench._FirstPaid()
    assert told(write(19_700), new, ".cover", SimpleNamespace(costs_more=True))[1]["tokens_in"] == 19_700


@pytest.fixture(scope="module")
def chrome():
    """One Chrome for every test here that draws a PDF, closed after the last of them: starting another
    costs a third of a second, and one left running is handed on to whatever test comes next."""
    from services.docgen import pdf

    yield
    pdf._discard()


@pytest.mark.skipif(not os.path.exists(os.environ.get("CHROME_BIN", "")),
                    reason="needs CHROME_BIN: a real Chrome renders the PDF")
@pytest.mark.usefixtures("chrome")
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

    from scripts.cvlab.run import _verdict, reviewer

    kept = tmp_path / "cell.verdict.json"
    kept.write_text(json.dumps({"overall_100": 61, "dimensions": {}, "reviewer": reviewer("cv")}),
                    encoding="utf-8")

    # `client=None` stands in for "no credential available": a cached verdict must not need one.
    got = _verdict(None, None, "<html></html>", {}, kept=kept, judge=True, force=False)

    assert got["overall_100"] == 61


def test_a_verdict_is_used_again_only_by_the_reviewer_that_wrote_it(tmp_path, monkeypatch):
    """Letters were read without their subject line until 2026-11-18, and one subject in seven claimed a
    speciality nobody had. A reviewer that changes (its model, its prompt, what it is shown) reads again:
    its marks are never averaged with the last one's."""
    import json

    from scripts.cvlab import run as bench

    kept = tmp_path / "cell.cover.verdict.json"
    kept.write_text(json.dumps({"overall_100": 61, "dimensions": {}}), encoding="utf-8")
    asked = []
    def judge_one(client, posting, text, report, theirs, *, kind, model=None, hire=False):
        asked.append(text)
        return {"overall_100": 70, "dimensions": {}}
    monkeypatch.setattr(bench, "judge_one", judge_one)

    got = bench._verdict(object(), None, "Subject: x", {}, kept=kept, judge=True, force=False, kind="cover")
    assert got["overall_100"] == 70 and asked == ["Subject: x"]
    assert json.loads(kept.read_text(encoding="utf-8"))["reviewer"] == bench.reviewer("cover")
    assert bench._verdict(object(), None, "Subject: x", {}, kept=kept, judge=True, force=False,
                          kind="cover")["overall_100"] == 70 and len(asked) == 1
    assert bench.reviewer("cover") != bench.reviewer("cv")
    monkeypatch.setattr(bench, "JUDGE_MODEL", "another-model")
    bench._verdict(object(), None, "Subject: x", {}, kept=kept, judge=True, force=False, kind="cover")
    assert len(asked) == 2


def test_asking_for_no_judgement_returns_none_rather_than_reading_the_cache(tmp_path):
    """`--dry-run` without `--judge` reports the mechanical checks only, on purpose: a verdict kept beside
    the document, which a run with `--judge` would use, stays out of it."""
    from scripts.cvlab.run import _verdict, reviewer

    kept = tmp_path / "cell.verdict.json"
    kept.write_text(json.dumps({"overall_100": 61, "dimensions": {}, "reviewer": reviewer("cv")}),
                    encoding="utf-8")
    assert _verdict(None, None, "<html></html>", {}, kept=kept, judge=True, force=False), "the premise"

    assert _verdict(None, None, "<html></html>", {}, kept=kept, judge=False, force=False) is None


# ── tools that are not ours (`checkers.py`) ─────────────────────────────────────────────────────

# A letter as the bench keeps it on disk from before 2026-11-26, when `build.assemble` still wrote a block
# for the company above it: the header, that block, the subject, then the letter. Read as it always was.
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
    # Its reviewer reads it as the person it is sent to does: the subject line first.
    assert letter_text(LETTER_PAGE, subject=True).startswith(
        "Subject: Data analyst specialising in operations\n\nDear Hiring Manager,")


def test_a_letter_with_no_subject_line_is_read_from_its_greeting_with_its_bold_title_whole():
    """A letter has no subject line since 2026-11-26 and names its job in bold. Its reviewers are shown it
    from the greeting, and a word in bold is one word with the comma after it: read with a space where each
    tag was, "Analyst ," would have been marked as the letter's own slip."""
    from scripts.cvlab.run import letter_text

    page = LETTER_PAGE.replace('<p class="letter-subject">Data analyst specialising in operations</p>\n', "")
    page = page.replace("<p>Second one.</p>", "<p>I want the <strong>Analyst</strong>, here.</p>")

    assert letter_text(page, subject=True) == ("Dear Hiring Manager,\n\nFirst paragraph here.\n\nI want the "
                                               "Analyst, here.\n\nKind regards,\nMaya Example")


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
    """Counted, not raised: a checker that raises is a site that failed, and leaves the same empty row."""
    asked = []
    monkeypatch.setitem(checkers.CHECKERS, "letter",
                        (lambda document: asked.append(document) or {"scores": {"score": 80.0}}, "cover"))

    assert checkers.measure(["letter"], _document(tmp_path)) == {}
    assert asked == [], "the letter checker was asked about a CV"
    assert checkers.measure(["letter"], _document(tmp_path, kind="cover")) == {"letter.score": 80.0}
    assert len(asked) == 1


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


def test_a_letter_is_reviewed_by_its_own_reviewer_who_is_shown_what_is_known_of_the_company():
    """Letters have a reviewer of their own (owner, 2026-11-16: "the same procedure" as the CV), and it is
    shown the company facts the wording may use, so it can tell a true reason from a made-up one. A mark is
    kept only with a line found in the letter, as the CV's reviewer's is."""
    from scripts.cvlab import run as bench

    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Analyst",
                             "company": "Acme", "description": "We need an analyst."})
    posting.company_facts = "Acme makes bicycles in Leeds."
    letter = "Dear Hiring Manager,\n\nI rebuilt the stock count for a bike shop.\n\nKind regards,\nMaya"
    heard = {}

    class Reviewer:
        def complete_text(self, *, model, system, messages, max_tokens):
            heard.update(system=system, prompt=messages[0]["content"])
            return json.dumps({"dimensions": {
                "why_here": {"score": 8, "evidence": "I rebuilt the stock count for a bike shop."},
                "opening": {"score": 9, "evidence": "a line that is not in the letter"},
                "six_second_pull": {"score": 2, "evidence": "Dear Hiring Manager,"}},
                "not_theirs": [], "would_reply": "yes", "one_line": "Good."})

    verdict = bench.judge_one(Reviewer(), posting, letter, {}, "Her documents.", kind="cover")
    assert heard["system"] == bench.LETTER_JUDGE_SYSTEM
    assert "Acme makes bicycles in Leeds." in heard["prompt"] and letter in heard["prompt"]
    assert set(verdict["dimensions"]) == {"why_here"}, "unquoted marks and a CV's dimension are dropped"
    assert verdict["overall_100"] == 80


def test_a_wording_reads_what_is_known_about_the_company_or_that_nothing_is():
    from scripts.cvlab import run as bench

    candidate = bench.Candidate.__new__(bench.Candidate)
    candidate.name, candidate.languages = "maya", ()
    candidate.user = SimpleNamespace(full_name="Maya Example", links=[])
    candidate.documents = {"cv": ["Maya Example\nEXPERIENCE\nAnalyst"]}
    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Analyst",
                             "company": "Acme", "description": "We need an analyst."})
    wording = "Write the letter. {{job}} <company>{{company}}</company> <letter></letter>"

    posting.company_facts = "Acme makes bicycles in Leeds."
    assert "<company>Acme makes bicycles in Leeds.</company>" in bench.request_for(
        candidate, posting, wording, "cover").prompt
    posting.company_facts = ""
    assert f"<company>{bench.build.NOTHING_MORE}</company>" in bench.request_for(
        candidate, posting, wording, "cover").prompt
    # Nothing read about it for the bench: what the posting itself says under "About us", as production
    # gathers it from the company's postings.
    posting.description = "About us\nAcme mends bicycles.\n\nThe role\nYou count stock."
    assert "<company>Acme mends bicycles.</company>" in bench.request_for(
        candidate, posting, wording, "cover").prompt
    assert "{{company}}" not in bench.request_for(candidate, posting, None, "cover").prompt


@pytest.mark.skipif(not os.path.exists(os.environ.get("CHROME_BIN", "")),
                    reason="needs CHROME_BIN: a real Chrome renders the PDF")
@pytest.mark.usefixtures("chrome")
def test_a_letter_has_ats_checks_of_its_own():
    """A letter through an ATS procedure too (owner, 2026-11-16): read whole, the name first, the employer and
    the posting's title in it, its length, one page.

    The employer is named in the letter's own words: the block above the subject names it in every letter,
    since code writes it, and was the only place a letter that never named it was found to (2026-11-18). The
    length is the one the docgen worker sends a letter back by, greeting, sign-off and name not in it: the
    bench counted them, 6 to 39 words more than the worker over 54 letters."""
    from scripts.cvlab import run as bench
    from services.docgen import pdf

    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Analyst (m/f/d)",
                             "company": "Acme Group | Moving stock since 1990",
                             "description": "We need an analyst."})

    def letter(weeks: int, opening: str = "At Acme we count stock. ") -> str:
        # Three words in the first paragraph, the opening's, then five a week.
        middle = f"<p>{opening}{'We count stock every week. ' * weeks}</p>"
        return LETTER_PAGE.replace("<p>Second one.</p>", middle)

    long_enough = letter(40)
    for page, failed in ((LETTER_PAGE, ["company", "length"]), (long_enough, []),
                         (long_enough.replace("Data analyst specialising", "Specialist"), ["title"]),
                         (letter(40, opening="We count stock. "), ["company"]),
                         # 178 words of letter, 185 with its greeting, sign-off and name.
                         (letter(34), ["length"]),
                         # Named as a person writes it, not as the board's field does (2026-11-18).
                         (letter(40, opening="At Acme Logistics we count stock. "), [])):
        document = pdf.render(page)
        report = pdf.inspect_pdf(document, kind="cover")
        passed = bench.ats_letter_checks(document, page, posting, report)
        assert [name for name, ok in passed.items() if not ok] == failed, page[:0]


def test_a_letter_names_the_job_roughly_as_the_posting_does():
    """Owner, 2026-11-19: the posting's title belongs in a letter, "perhaps not the exact title but it should
    be in there roughly the same". The bench asked for it word for word, and failed letters a person reads
    as naming the job: four of the 36 live and v3 letters of 2026-11-18, in English and German."""
    from scripts.cvlab.checkers import names_title

    for text, title in (
            ("why I am applying for the Clinical Nurse Specialist for Endometriosis post",
             "Clinical Nurse Specialist Endometriosis"),
            ("the Apprentice/Trainee Client Support Administrator role",
             "Apprentice Client Support Administrator"),
            ("Ich bewerbe mich als Mitarbeiterin im Kundenservice für Krankenkassen.",
             "Mitarbeiter Kundenservice für Krankenkassen und weitere Themen (w/m/div.)"),
            ("als Elektroniker im Bereich Gebäudetechnik",
             "Elektriker / Elektroniker (m/w/d) im Bereich Gebäudetechnik"),
            ("als Pflegefachassistentin bei Alloheim", "Pflegefachassistenz (m/w/d)"),
            ("az Accounts Receivable Coordinator pozícióra", "Accounts Receivable Coordinator"),
            ("Subject: Staff Nurse specialising in theatres", "Bank Staff Nurse/ODP"),
            # A board's run of keywords: the letter names the job, not the first keyword (2026-11-27).
            ("the Go Developer role that 1dea is recruiting for",
             "Golang | Go Developer | Python | Cloud | IoT | security")):
        assert names_title(text, title), title
    # Another job, the title's words apart, a shorter or a longer word, or a translation, do not name it.
    for text, title in (
            ("the Server role at Las Iguanas", "Waiting Team Member"),
            ("the Vehicle Technician role", "Vehicle Mechanic"),
            ("I supported residents as a care worker", "Support Worker - Beaumanor"),
            ("I worked at the reception of a hotel", "Receptionist"),
            ("I have written Python for four years",
             "Golang | Go Developer | Python | Cloud | IoT | security"),
            ("I ran office management", "Office Manager - FTC 12 Months"),
            ("as an accredited checking technician", "Accuracy Checking Pharmacy Technician (Full-time)"),
            ("a kintlévőség-kezelési koordinátori munkakör", "Accounts Receivable Coordinator")):
        assert not names_title(text, title), title


def test_a_letter_is_counted_on_calling_its_employer_what_people_call_it():
    """Owner, 2026-11-19: "not the long posted versions but from bosch group we should say bosch". Counted
    where the board's field has something to leave out, and only there."""
    from scripts.cvlab import run as bench

    rettenmaier = "J. RETTENMAIER &amp; SÖHNE GmbH + Co KG"
    for text, company, said in (("my work at Bosch in Magdeburg", "Bosch Group", 1),
                                ("bei der Bosch Group in Magdeburg", "Bosch Group", 0),
                                ("bei der RETTENMAIER & SÖHNE GmbH + Co KG", rettenmaier, 0),
                                ("bei J. Rettenmaier & Söhne in Rosenberg", rettenmaier, 1),
                                ("at VNOM | We get the job done", "VNOM | We get the job done", 0),
                                ("at VNOM in Bleiswijk", "VNOM | We get the job done", 1),
                                ("at Screwfix in Leeds", "Screwfix", None)):
        assert bench.says_company_as_people_do(text, company) == said, text


@pytest.mark.skipif(not os.path.exists(os.environ.get("CHROME_BIN", "")),
                    reason="needs CHROME_BIN: a real Chrome renders the PDF")
@pytest.mark.usefixtures("chrome")
def test_a_letter_is_checked_in_every_look_a_person_can_pick():
    """Owner, 2026-11-18: "make sure all the formats that we can choose the cover letter for also works with
    it". The same letter drawn in each letter look and read as an ATS reads it, and a fault named in every
    look it is in."""
    from libs import templates
    from scripts.cvlab import run as bench
    from services.docgen import build

    candidate = bench.Candidate.__new__(bench.Candidate)
    candidate.name, candidate.languages = "maya", ()
    candidate.user = SimpleNamespace(full_name="Maya Example", links=[])
    candidate.documents = {"cv": ["Maya Example\nmaya@example.com\n07700 900123\nEXPERIENCE\nAnalyst, Acme"]}
    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Analyst",
                             "company": "Acme Ltd", "description": "We need an analyst."})
    page = build.assemble(bench.request_for(candidate, posting, None, "cover"), (
        '<p class="letter-subject">Analyst specialising in stock</p><div class="letter">'
        f"<p>Dear Hiring Manager,</p><p>At Acme we count stock. {'We count stock every week. ' * 40}</p>"
        '<p class="closing">Kind regards,</p><p class="signature">Maya Example</p></div>'))
    untitled = page.replace("Analyst specialising", "Specialist")

    assert bench.every_look(candidate, posting, page, "cover") == {"ats_every_look": 100.0,
                                                                   "looks_failed": {}}
    assert bench.every_look(candidate, posting, untitled, "cover")["looks_failed"] == {
        look.id: ["title"] for look in templates.of_kind("cover")}


def test_a_cover_letter_cell_is_measured_as_a_letter(tmp_path, monkeypatch):
    """A letter is pasted as a letter, its own letter is the baseline, and the CV's counts stay off it.
    Their own letter is asked about once, whatever the sample."""
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


def test_the_hiring_managers_read_is_kept_beside_the_letter_reviewers_with_the_chance_of_an_interview(
        tmp_path, monkeypatch):
    """Owner, 2026-11-21: a filter "reverse engineering the cover letter as a hiring manager would want to see
    it", from what the posting needs and the best case the person's record makes. It is a reviewer of its own:
    it reads its own prompt, its verdict is kept beside the letter reviewer's and never over it, it keeps only
    its own marks, and it says the chance of an interview."""
    from scripts.cvlab import run as bench

    candidate = bench.Candidate.__new__(bench.Candidate)
    candidate.name, candidate.languages = "maya", ()
    candidate.user = SimpleNamespace(full_name="Maya Example", links=[])
    candidate.documents = {"cv": ["Maya Example\nEXPERIENCE\nAnalyst"],
                           "cover_letter": ["Dear team, my own."]}
    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Analyst",
                             "company": "Acme", "description": "We need an analyst."})
    wording = "Write the letter. {{job}} <letter></letter>"
    folder = bench.kept_in(tmp_path, "claude-haiku-5-5", "medium", wording)
    (folder / "maya__live__job__1.cover.html").write_text(LETTER_PAGE, encoding="utf-8")
    theirs = folder / "maya__live__job__1.cover.verdict.json"
    theirs.write_text(json.dumps({"overall_100": 50, "reviewer": bench.reviewer("cover")}), encoding="utf-8")
    monkeypatch.setattr(bench, "rendered",
                        lambda page, label, kind: (None, bench.pdf.PdfReport(pages=None, chars=0)))
    line = bench.letter_text(LETTER_PAGE).splitlines()[2]
    read = []

    class HiringManager:
        def complete_text(self, *, model, system, messages, max_tokens):
            read.append(messages[0]["content"])
            return json.dumps({"dimensions": {"best_case": {"score": 7, "evidence": line},
                                              "why_here": {"score": 2, "evidence": line}},
                               "not_theirs": [], "interview": 65, "one_line": "Likely."})

    args = SimpleNamespace(kind="cover", dry_run=True, force=False, judge=True, checkers=[], reviewer="hire",
                           model="claude-haiku-5-5", effort="medium")
    row = bench._run_cell(None, candidate, posting, variant="live", sample=1, template=wording, out=tmp_path,
                          args=args, judge=HiringManager())
    assert "The hire you want" in read[0] and "Dear team, my own." in read[0]
    assert row["interview"] == 65 and row["dimensions"] == {"best_case": 7} and row["overall_100"] == 70
    assert json.loads(theirs.read_text(encoding="utf-8"))["overall_100"] == 50
    kept = json.loads((folder / "maya__live__job__1.cover.hire.json").read_text(encoding="utf-8"))
    assert kept["reviewer"] == bench.reviewer("cover", hire=True) != bench.reviewer("cover")
    # Asked again, the kept read is used and nothing is paid for.
    again = bench._run_cell(None, candidate, posting, variant="live", sample=1, template=wording,
                            out=tmp_path, args=args, judge=HiringManager())
    assert again["interview"] == 65 and len(read) == 1


def test_a_letters_reviewer_says_which_needs_it_answers_and_code_counts_them():
    """Owner, 2026-11-26: "the way recruiters measure that is by comparing the cover letter, to the job
    description", and a letter read "without much reference to what the job is actually about". Each reviewer
    lists what the employer needs most and the line that answers each, and code counts them (rules before AI):
    only where the line is in the letter, only against what the person's record can answer, and a need the
    letter answers that the record cannot is counted apart, as the claim bigger than the record it is."""
    from scripts.cvlab import run as bench

    posting = bench.Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Analyst",
                             "company": "Acme", "description": "We need an analyst."})
    letter = ("Dear Hiring Manager,\n\nI rebuilt the stock count for a bike shop.\n\nI report on sales each "
              "week.\n\nKind regards,\nMaya")
    needs = [{"need": "stock control", "in_their_record": True, "answered": "yes",
              "line": "I rebuilt the stock count for a bike shop."},
             {"need": "weekly reporting", "in_their_record": True, "answered": "partly",
              "line": "I report on sales each week."},
             {"need": "forecasting", "in_their_record": True, "answered": "yes", "line": "not in the letter"},
             {"need": "SQL", "in_their_record": True, "answered": "no", "line": ""},
             {"need": "managing a team", "in_their_record": False, "answered": "yes",
              "line": "I report on sales each week."}]

    class Reviewer:
        def complete_text(self, **call):
            return json.dumps({"dimensions": {
                "wants_cv": {"score": 6, "evidence": "I report on sales each week."},
                "answers_job": {"score": 4, "evidence": "I report on sales each week."}}, "needs": needs})

    for hire, kept in ((False, {"wants_cv"}), (True, {"answers_job"})):
        verdict = bench.judge_one(Reviewer(), posting, letter, {}, "Her documents.", kind="cover", hire=hire)
        assert set(verdict["dimensions"]) == kept, "each reviewer keeps its own marks"
        assert verdict["needs_can"] == 4 and verdict["needs_answered"] == 1.5
        assert verdict["needs_share"] == 0.375 and verdict["needs_unbacked"] == 1
    # A reviewer that lists no needs (a CV's) is not counted as answering none.
    assert bench.read_verdict(json.dumps(REVIEW), cvchecks.text_of(CV))["needs_share"] is None


def test_a_letter_is_counted_on_how_far_it_speaks_of_its_posting():
    """The same complaint, by rule and with no reviewer: of the work a letter tells, between its opening and
    its last two paragraphs, how many of its words are the posting's too; the numbers in it; and the names in
    it the posting never uses. The owner's own letter gave 17 such names, an Ubuntu version among them, to a
    posting that asked for Python services and automation."""
    from scripts.cvlab.run import Posting, answering

    posting = Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Infrastructure Engineer",
                       "company": "Acme", "description": "You will build Python services and automate our "
                       "infrastructure, own releases through the pipeline and keep production reliable."})

    def letter(work: str) -> str:
        return ('<div class="letter"><p>Dear Hiring Manager,</p><p>Opening at Acme.</p>'
                f"<p>{work}</p><p>Why I want this at Acme.</p><p>What I would build there.</p>"
                '<p class="closing">Kind regards,</p><p class="signature">Maya Example</p></div>')

    answers = answering(letter("I build Python services and automate the infrastructure they run on. I own "
                               "their releases through the pipeline and keep production reliable."), posting)
    recites = answering(letter("I set up an Ubuntu 24.04 server with fail2ban and UFW. Deduplication handles "
                               "10k records in 1ms with a classifier at 0.82, tested in Playwright."),
                        posting, "Maya Example")

    assert answers["posting_words"] > 0.6 and recites["posting_words"] < 0.2
    assert recites["figures"] == 4 and answers["figures"] == 0
    # Ubuntu, fail2ban, UFW and Playwright.
    assert recites["names_unasked"] == 4 and answers["names_unasked"] == 0
    # Python is the posting's own word, Acme the company and Maya the person: none is a name it never uses.
    german = Posting({"slug": "job", "group": "g", "candidate": "maya", "title": "Elektriker",
                      "description": "Wir suchen einen Elektriker für die Gebäudetechnik in unserem Werk, "
                      "mit abgeschlossener Ausbildung und Erfahrung in der Instandhaltung von Anlagen."})
    assert answering(letter("I maintain the plant."), german)["posting_words"] is None


def test_what_other_tools_said_reaches_the_comparison():
    """An outside measure that moved must be in the paired table, or a prompt is judged on ours alone."""
    from scripts.cvlab.run import scoreboard

    rows = [{"candidate": "x", "variant": variant, "job": "j", "group": "g", "sample": 1,
             "overall_100": None, "resymatch.match": match}
            for variant, match in (("live", 40.0), ("v2", 55.0))]
    board = scoreboard(rows)

    assert "| live | 1 | 40.0 |" in board and "| v2 | 1 | 55.0 |" in board, "their means, by variant"
    assert "| resymatch.match | +15.00" in board


def test_a_phrase_one_model_writes_for_many_people_is_its_formula_and_one_persons_own_phrase_is_not():
    """Owner, 2026-11-19: no AI language. A phrase in many people's letters is the model's, not theirs: Sol
    lean wrote "what draws me to" for 8 of 19 people, Haiku "I would like to bring that" for 13."""
    from scripts.cvlab.formulas import formulas

    work = ["I drove forklifts on nights", "I ran a hotel front desk", "I cut hair in Leeds",
            "I nursed on a ward"]
    why = ["its depot", "its guests", "its salon", "its patients"]
    letters = {f"p{n}/job": f"Dear Hiring Manager, {work[n]}. What draws me to you is {why[n]}."
               for n in range(4)}
    letters["p4/job"] = "Dear Hiring Manager, I fixed boilers for nine years and my van is my office."
    letters["p4/other"] = "Dear Hiring Manager, I fixed boilers for nine years and my van is my office."

    found = {phrase: count for count, phrase in formulas(letters)}

    # The longest form, once; one person's two letters are one person; the greeting is asked for.
    assert found == {"what draws me to you is its": 4}


def test_every_look_names_people_and_postings_the_bench_has_and_one_posting_reads_for_two(tmp_path):
    """Owner, 2026-11-27: a wording is judged by the session reading a few people's documents. The look is
    that set, kept in the tree: a pair naming a person or a posting that is not there is a document nobody
    reads, and one posting has to be writable for two people (the recruiter's, for both developers)."""
    from scripts.cvlab import look, run

    names = [path.stem for path in look.LOOKS.glob("*.json")]
    assert "cover" in names
    for name in names:
        spec = look.look(name)
        written = look.jobs(name, tmp_path / name)
        assert len(written) == len(spec["pairs"]) == len({path.name for path in written}) > 0
        for person in {person for person, _ in spec["pairs"]}:
            assert (look.PANEL / person / "candidate.json").exists(), person
            if spec["kind"] == "cover":
                assert any((look.PANEL / "letters" / person).glob("*.txt")), f"{person} has no weak letter"
            theirs = run.load_postings(tmp_path / name, person, None)
            assert len(theirs) == sum(who == person for who, _ in spec["pairs"])
            assert all(posting.description.strip() for posting in theirs)
    shared = [slug for _, slug in look.look("cover")["pairs"]]
    assert len(shared) > len(set(shared)), "the cover look reads one posting for two people"


def test_a_look_prints_each_document_whole_and_marks_what_a_reader_should_see_first(tmp_path, capsys):
    """The counts are a reading aid: a letter with the board's dash in its bold title, a sentence that begins
    with the prompt's own name for a paragraph and no line asking to talk is marked on each, and the letter
    that has none of them is not. Both are printed whole, because the reading is the judgement."""
    from scripts.cvlab import look

    def page(*paragraphs: str) -> str:
        inner = "".join(f"<p>{each}</p>" for each in ("Dear Hiring Manager,", *paragraphs))
        return (f'<html><body><div class="letter">{inner}<p class="closing">Kind regards,</p>'
                '<p class="signature">Sam Doe</p></div></body></html>')

    good = page("I want to keep boilers going, and the <strong>Heating Engineer</strong> job at Northwind "
                "is that work.", "I've done six calls a day for years.",
                "I'd like to get to people whose heating has failed. Could we talk about your rota?")
    bad = page("The <strong>Heating Engineer \N{EN DASH} Domestic &amp; Commercial</strong> job is open.",
               "What I'd bring is forty years in the trade.", "Thank you for reading this.")
    slug = look.look("cover")["pairs"][0][1]
    rows = []
    for person, text in (("plumber", good), ("care-worker", bad)):
        rows.append({"variant": "v9", "candidate": person, "job": slug, "sample": 1, "model": "some-model",
                     "prompt_version": "abc", "fault_list": [], "ai_language": 0, "pages": 1})
        kept = tmp_path / "some-model@medium" / "abc" / f"{person}__v9__{slug}__1.cover.html"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(text, encoding="utf-8")
    (tmp_path / "results.json").write_text(json.dumps(rows), encoding="utf-8")

    assert look.read("cover", tmp_path / "results.json", "v9", tmp_path) == 2
    printed = capsys.readouterr().out
    lines = printed.splitlines()[:3]
    head, bad_line, good_line = ([cell.strip() for cell in line.split("|")] for line in lines)
    marks = dict(zip(head, zip(bad_line, good_line, strict=True), strict=True))

    for count in ("opens on why", "asks to talk", "board punctuation in it", "the prompt's part names"):
        bad_cell, good_cell = marks[count]
        assert "!" in bad_cell and "!" not in good_cell, count
    assert "Could we talk about your rota?" in printed and "What I'd bring is forty years" in printed
    assert "**Heating Engineer**" in printed, "the bold title is shown as bold"


def test_two_wordings_are_set_side_by_side_unnamed_and_a_readers_ranking_is_given_back_by_wording(
        tmp_path, capsys):
    """Owner, 2026-11-28, of the wording made live the night before: "the v37 read much better". It had been
    read letter by letter against a list and never beside the letter it replaced. So the documents two
    wordings wrote for the same person and posting are set side by side for a fresh reader, with nothing in
    them saying which is which, and that reader's ranking comes back as each wording's place."""
    from scripts.cvlab import look

    def page(body: str) -> str:
        return (f'<html><body><div class="letter"><p>Dear Hiring Manager,</p><p>{body}</p>'
                '<p class="closing">Kind regards,</p><p class="signature">Sam Doe</p></div></body></html>')

    versions = {"live": "aaa111", "next": "bbb222"}
    for name, version in versions.items():
        for person in ("plumber", "nurse"):
            kept = tmp_path / "some-model@medium" / version / f"{person}__{name}__a-job__1.cover.html"
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_text(page(f"The {name} wording wrote this for the {person}."), encoding="utf-8")
    # A document only one wording wrote has nothing to be read beside.
    (tmp_path / "some-model@medium" / "aaa111" / "chef__live__a-job__1.cover.html").write_text(
        page("Nobody to compare."), encoding="utf-8")

    assert look.blind(tmp_path, versions, tmp_path / "sets.md", tmp_path / "key.json", seed=3) == 2
    sets, key = (tmp_path / "sets.md").read_text(), json.loads((tmp_path / "key.json").read_text())
    assert "Nobody to compare" not in sets and "aaa111" not in sets and "bbb222" not in sets
    assert {case["person"] for case in key.values()} == {"plumber", "nurse"}
    for number, case in key.items():
        for label in "AB":
            written = sets.split(f"### Letter {number}{label}\n")[1].split("###")[0]
            assert f"The {case[label]} wording wrote this for the {case['person']}." in written

    # The reader ranks "next" first in both sets, whatever letter it was given, and marks the opening, the
    # part a skimming reader reads, in one of them.
    answers = [{"set": int(number), "clear": True,
                "ranking": sorted("AB", key=lambda label, case=case: case[label] != "next"),
                "flow": {label: 8 if case[label] == "next" else 5 for label in "AB"}}
               for number, case in key.items()]
    first = key[str(answers[0]["set"])]
    answers[0]["opening"] = {label: 9 if first[label] == "next" else 3 for label in "AB"}
    (tmp_path / "answers.json").write_text(json.dumps(answers), encoding="utf-8")
    found = look.unblind(tmp_path / "answers.json", tmp_path / "key.json")["some-model@medium"]
    assert found["next"] == {"first": 2, "of": 2, "place": 1.0, "flow": 8.0, "opening": 9.0}
    assert found["live"] == {"first": 0, "of": 2, "place": 2.0, "flow": 5.0, "opening": 3.0}
    assert "next > live" in capsys.readouterr().out
