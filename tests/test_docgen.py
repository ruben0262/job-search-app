"""The document writer: what reaches the model, what is kept of its answer, and what is never delivered."""
from __future__ import annotations

import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import pytest

from libs import templates
from libs.llm import cli
from services.docgen import build, checks, pdf, run

MERIDIAN = templates.find("cv", "meridian")
KESTREL = templates.find("cv", "kestrel")
LETTER = templates.find("cover", "meridian")

# The invisible character these tests are about, named and escaped.
ZERO_WIDTH = "\u200b"

# An uploaded CV as text, the way `libs.documents.extract` leaves one.
CV_TEXT = """Alice Example
alice@example.com | +49 151 2345 6789 | Berlin, Germany

EXPERIENCE
Backend Engineer, Northwind    2021 - Present
- Built the billing service in Python and Postgres
- Cut checkout latency to 40ms at p99 for 1,200 shops

EDUCATION
BSc Computer Science, TU Berlin    2017
"""
SECOND_CV = "Alice Example\nData engineer CV\nEXPERIENCE\nAnalyst, Contoso 2018 - 2021\n- Ran the warehouse"
LETTER_TEXT = "Dear team, I build billing systems that do not page anyone at night. Alice Example"

# What the model answers: the middle of a CV, in the markup, inside its tags.
CV_BODY = """<p class="profile">Backend engineer who cut checkout latency to 40ms.</p>
<div class="section"><div class="section-title">Experience</div>
  <div class="entry"><div class="entry-head">
    <span class="entry-title">Backend Engineer · Northwind · Berlin</span>
    <span class="entry-date">2021 - Present</span></div>
    <ul><li>Built the billing service in Python and Postgres</li></ul></div>
</div>
<div class="section"><div class="section-title">Education</div></div>"""
ANSWER = f"<cv>\n{CV_BODY}\n</cv>"
LETTER_BODY = """<p class="letter-subject">Application for Backend Engineer</p>
<div class="letter"><p>Dear Hiring Manager,</p><p>I cut checkout latency to 40ms at Northwind.</p>
<p class="closing">Kind regards,</p><p class="signature">Alice Example</p></div>"""
LETTER_ANSWER = f"<letter>{LETTER_BODY}</letter>"

# Whatever the payer resolved to; the fake client answers the same either way.
MODEL = "test-model"


class FakeJob:
    """The corpus row a build reads, without a database."""

    def __init__(self, description="We need a backend engineer. Python, Postgres, on-call."):
        self.id = 7
        self.url = "https://example.test/jobs/7"
        self.title = "Backend Engineer"
        self.company = "Northwind"
        self.location = "Berlin"
        self.source = "linkedin"
        self.description = description
        self.workplace = "hybrid"
        self.language = "de"


def _user(**fields):
    values = {"id": uuid.uuid4(), "full_name": "Alice Example", "links": [], "cv_template": None,
              "cover_template": None, **fields}
    return type("U", (), values)()


DOCUMENTS = {"cv": [CV_TEXT, SECOND_CV], "cover_letter": [LETTER_TEXT],
             "history": ["I want to move into platform work."], "reference": ["Alice is reliable. Bob, CTO"]}


def _request(kind="cv", *, documents=None, job=None, user=None, look=None, **kwargs):
    return build.prepare(kind, user=user or _user(), job=job or FakeJob(),
                         documents=DOCUMENTS if documents is None else documents,
                         look=look or (MERIDIAN if kind == "cv" else LETTER), **kwargs)


class FakeClient:
    """Records what it was asked, answers with whatever the test queued."""

    def __init__(self, *replies):
        self.replies = list(replies) or [ANSWER]
        self.calls: list[dict] = []

    def complete_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


# ── what reaches the model ────────────────────────────────────────────────────────────────────

def test_a_chatgpt_letter_is_written_lean_and_a_cv_as_before():
    """Owner, 2026-11-19: "yes use sol lean for letters". A letter goes to Codex with this platform's
    instructions alone, about half of the person's ChatGPT plan for the same letter (CHANGELOG); a CV as
    before until it is measured lean. The client is left as its caller had it."""
    told, replies = [], [LETTER_ANSWER, ANSWER]

    class Codex(cli.CliClient):
        def complete_text(self, **call):
            told.append(call.get("lean"))
            return replies.pop(0)

    client = Codex("codex_login", "{}", [].append)
    build.draft(client, _request("cover"), model="gpt-6.2-sol")
    build.draft(client, _request("cv"), model="gpt-6.2-sol")

    assert told == [True, None] and client.lean is None


def test_a_prompt_that_asks_what_a_page_holds_is_told_the_picked_looks_own_number():
    """Owner, 2026-11-14: tell the prompt the picked look's own number, so a CV is written for the page it
    will be drawn on. Meridian's is the 400 the prompt has always said."""
    asks = "One page holds about {{page_words}} words. <cv></cv>"

    assert "about 400 words" in _request(template=asks, look=MERIDIAN).prompt
    assert "about 310 words" in _request(template=asks, look=templates.find("cv", "halcyon")).prompt


def test_the_cv_prompt_everyone_is_written_with_is_told_their_looks_page_and_names_the_job():
    """The wording made live on 2026-11-15 (owner; CHANGELOG): it is told the picked look's own page,
    where the one before said 400 to everybody, and it has the profile name the job in the posting's
    title, which is what took the ATS checks from 87 to 99 of 100."""
    prompt = _request(look=templates.find("cv", "halcyon")).prompt

    assert "about 310 words" in prompt and "about 400 words" not in prompt and "{{" not in prompt
    assert "in the posting's own title" in _request(look=MERIDIAN).prompt


def test_an_ampersand_is_never_shown_as_its_code():
    """The owner, 2026-11-15: "&amp;" must not occur. A reviewer was shown "Fennel &amp; Flint" by the
    bench, and the same could reach a person's page two ways: a model that writes the code twice, and a
    job whose employer was kept with the code in it, which the letter's address then showed as typed."""
    for said in ("Fennel & Flint", "Fennel &amp; Flint", "Fennel &amp;amp; Flint"):
        page = build.clean(f'<p class="profile">Chef at {said}.</p>')
        assert page == '<p class="profile">Chef at Fennel &amp; Flint.</p>', said
        assert build.text_of(page) == "Chef at Fennel & Flint."

    job = FakeJob()
    job.company, job.title = "Fennel &amp; Flint", "R&amp;D Chef"
    letter = _request("cover", job=job)
    assert "Fennel & Flint" in letter.prompt and "R&D Chef" in letter.prompt and "&amp;" not in letter.prompt
    assert letter.company == "Fennel & Flint"


def test_a_subject_line_the_model_still_writes_is_left_out_of_the_letter():
    """Owner, 2026-11-26: no subject line above a letter ("Python developer with secured Linux deployment and
    Docker Compose experience" was theirs), the job named in the letter's own words instead. The prompt asks
    for none, and code decides it (CLAUDE.md §0): a letter is its greeting to its signature, so a subject a
    model writes from habit, or an address, never reaches the page."""
    written = build.answer(LETTER_ANSWER, "cover")

    assert written.startswith('<div class="letter"><p>Dear Hiring Manager,</p>')
    assert "Application for Backend Engineer" not in written and "letter-subject" not in written
    addressed = LETTER_ANSWER.replace("<letter>", "<letter><p>Northwind<br>Berlin</p>")
    assert build.answer(addressed, "cover") == written


def test_a_job_title_is_written_as_it_reads_in_a_sentence_without_the_boards_punctuation():
    """Owner, 2026-11-27: "take the punctuation out of the title", the dash in it for one. A board strings a
    title's parts with a dash or a bar and an ampersand, and the letter set it in bold as the board had it.
    Code decides it (CLAUDE.md §0), whatever the model wrote; a hyphen or an ampersand inside a word stays."""
    def written(title: str) -> str:
        letter = ('<div class="letter"><p>Dear Hiring Manager,</p>'
                  f"<p>I want the <strong>{title}</strong> role.</p></div>")
        answered = build.answer(f"<letter>{letter}</letter>", "cover")
        return re.search(r"<strong>(.*?)</strong>", answered).group(1)

    assert written("Software Engineer \u2013 Python & Infrastructure Automation") == (
        "Software Engineer, Python and Infrastructure Automation")
    assert written("Housekeeping - Room Attendant") == "Housekeeping, Room Attendant"
    assert written("Go Developer | Python | Cloud") == "Go Developer, Python, Cloud"
    assert written("Part-time R&D Engineer") == "Part-time R&amp;D Engineer"


def test_a_job_title_in_bold_is_counted_as_the_words_it_is():
    """A letter sets its job title in bold (owner, 2026-11-26: "bold the role wherever it will appear"). Read
    with a space where each tag was, "<strong>Backend Engineer</strong>," gave its comma away as a word of
    its own, in the word count a letter is held to and in what a reviewer is shown."""
    letter = _letter("I would like the <strong>Backend Engineer</strong>, <em>remote</em>, role at "
                     "Northwind.")

    assert checks.letter_words(letter) == 10
    assert build.text_of(letter).count("Backend Engineer, remote, role") == 1


def test_a_letters_writer_is_given_what_the_company_says_of_itself_or_told_nothing_more_is_known():
    """Owner, 2026-11-27: a letter is a motivation letter, and why this employer needs something true about
    it. What the platform gathered about the company (`libs.corpus.companies`) reaches the prompt's own
    place for it; a company it knows nothing more of is said to be so, and never left as a bare slot."""
    wording = "Write the letter. {{job}} <about_the_company>{{company}}</about_the_company> <letter></letter>"

    known = _request("cover", template=wording, company="Northwind sells wind to forty towns.")
    assert "<about_the_company>Northwind sells wind to forty towns.</about_the_company>" in known.prompt
    unknown = _request("cover", template=wording)
    assert f"<about_the_company>{build.NOTHING_MORE}</about_the_company>" in unknown.prompt


def test_a_letter_begins_at_its_greeting_with_no_block_for_the_company_above_it():
    """Owner, 2026-11-26: without the company and its place above it the letter "would look smoother". Code
    wrote that block from the board's fields as they came, so a letter for a recruiter's client was headed
    with the recruiter and a board's place ("Radley James", "Vilnius, Vilniaus, Lithuania"). Nothing stands
    between the header and what the model wrote now, whatever the job's company and place are."""
    written = build.answer(LETTER_ANSWER, "cover")
    page = build.assemble(_request("cover"), written)

    after_header = page.split('<hr class="header-rule">', 1)[1]
    assert after_header.strip().startswith(written.strip())
    assert "letter-to" not in page and "letter-meta" not in page and "Berlin" not in after_header
    # The letter's own words are still checked against the company as the board gave it.
    job = FakeJob()
    job.company = "Bosch Group"
    assert _request("cover", job=job).company == "Bosch Group"


def test_the_prompt_carries_the_job_and_everything_they_switched_on():
    """The owner's plan (2026-11-10): one prompt with all of it. Each CV apart, their About you, their
    other files, the whole posting, and the day it is."""
    prompt = _request(today=datetime(2026, 11, 11, tzinfo=UTC)).prompt

    for text in (CV_TEXT.strip(), SECOND_CV, "I want to move into platform work.", "Alice is reliable",
                 "We need a backend engineer. Python, Postgres, on-call.", "11 November 2026"):
        assert text in prompt, text
    assert "=== CV 1 of 2 ===" in prompt and "=== CV 2 of 2 ===" in prompt
    assert "Location: Berlin, Hybrid" in prompt and "The posting is written in: German" in prompt
    assert "{{" not in prompt


def test_a_cv_is_written_from_their_cvs_and_a_letter_from_their_letters_too():
    """A letter needs their record and their voice; a CV needs only the record."""
    cv, letter = _request("cv").prompt, _request("cover").prompt

    assert LETTER_TEXT not in cv
    assert LETTER_TEXT in letter and CV_TEXT.strip() in letter
    assert "Their name is Alice Example." in letter


def test_their_languages_are_what_their_cv_says_and_what_their_search_accepts():
    prompt = _request(languages=("en", "de")).prompt

    assert "The jobs they are looking for are in: English, German." in prompt


def test_a_long_posting_is_capped_not_dropped():
    prompt = _request(job=FakeJob(description="x" * (build.POSTING_MAX_CHARS + 5_000))).prompt

    assert "x" * build.POSTING_MAX_CHARS in prompt
    assert "x" * (build.POSTING_MAX_CHARS + 1) not in prompt
    assert f"cut at {build.POSTING_MAX_CHARS} characters" in prompt


def test_their_own_text_is_never_read_as_a_place_to_fill():
    """One pass over the prompt: a CV that says "{{name}}" says it, and is not filled in."""
    prompt = _request(documents={"cv": ["Alice Example\nTemplating with {{name}} and {{job}}"]}).prompt

    assert "Templating with {{name}} and {{job}}" in prompt


def test_a_prompt_with_a_place_nothing_fills_fails_before_anything_is_sent():
    """The silent failure here would be a model asked to invent whatever the slot was for."""
    with pytest.raises(KeyError):
        _request(template="Write it for {{nobody}}.")


def test_a_variant_prompt_is_filled_the_same_way():
    """The CV bench measures other wordings through the same fill."""
    prompt = _request(template="Job:\n{{job}}\nCV:\n{{cvs}}").prompt

    assert prompt.startswith("Job:\nJob title: Backend Engineer")


# ── what is kept of the answer ────────────────────────────────────────────────────────────────

def test_only_the_markup_survives():
    """No style, script, comment, invisible character or class of its own reaches the page: nothing
    that could hide text from the reader and show it to an ATS."""
    dirty = ('<p class="profile glow" style="color:#fff" onclick="x()">Backend <!-- note -->engineer'
             f'{ZERO_WIDTH}</p><script>alert(1)</script><style>p{{display:none}}</style>'
             '<div class="section"><span style="font-size:0">python python</span>'
             '<a href="javascript:alert(1)">bad</a><a href="https://example.com">good</a><table><tr><td>'
             'cell</td></tr></table></div>')

    kept = build.clean(dirty)

    assert kept == ('<p class="profile">Backend engineer</p><div class="section"><span>python python</span>'
                    '<a>bad</a><a href="https://example.com">good</a>cell</div>')


def test_the_white_space_of_the_models_markup_is_not_text():
    """Every look keeps the white space it is given, so that a wrapped line keeps its space for a reader
    that takes a PDF's characters as they are (`templates.EVERY_LOOK`, 2026-11-15). A line break or an
    indent in the model's markup must then not be one on the page."""
    written = ('<p class="profile">\n  Backend engineer who cut\n  latency to <strong>40ms</strong> for '
               'shops.\n</p>\n\n<div class="section">\n  <div class="section-title">Skills</div>\n  <ul>\n'
               "    <li><strong>Tools:</strong> Python, Postgres</li>\n    <li> On\n      call </li>\n"
               "  </ul>\n</div>")

    assert build.clean(written) == (
        '<p class="profile">Backend engineer who cut latency to <strong>40ms</strong> for shops.</p>'
        '<div class="section"><div class="section-title">Skills</div><ul>'
        "<li><strong>Tools:</strong> Python, Postgres</li><li>On call</li></ul></div>")


def test_the_answer_is_the_last_complete_one_and_nothing_around_it():
    """A model that works aloud may write the tag in its working before its answer."""
    reply = (f'Plan: the <cv> should lead with billing.\n<cv><p class="profile">Draft</p></cv>\n'
             f"Then:\n{ANSWER}\nDone.")

    assert build.answer(reply, "cv").startswith('<p class="profile">Backend engineer')


def test_a_line_of_the_answer_quoted_after_it_is_not_taken_for_the_document():
    """One CV in 44 written for the panel was a single quoted sentence: the model went on after its CV,
    quoting its profile between the tags, and the last pair of tags was the answer (2026-11-14)."""
    quoted = '<cv><p class="profile">Backend engineer who built the billing service...</p></cv>'
    quoted_letter = "<letter><p>Dear Hiring Manager,</p></letter>"

    assert build.answer(f"{ANSWER}\nI checked the profile: {quoted}", "cv") == build.answer(ANSWER, "cv")
    assert build.answer(f"{LETTER_ANSWER}\nIt opens {quoted_letter}", "cover") == build.answer(LETTER_ANSWER,
                                                                                               "cover")
    # And when a line is all there is, there is no document: the build fails and is tried again.
    with pytest.raises(build.BuildFailed):
        build.draft(FakeClient(f"The CV starts {quoted} and then <cv><p>ran out"), _request(), model=MODEL)


@pytest.mark.parametrize("reply", ["", "Here is the CV.", "<cv><p>cut off", "<cv>   </cv>"])
def test_a_reply_with_no_complete_answer_is_a_build_failure(reply):
    with pytest.raises(build.BuildFailed):
        build.draft(FakeClient(reply), _request(), model=MODEL)


def test_em_dashes_leave_a_cvs_sentences_and_stay_in_its_titles():
    body = build.answer('<cv><p class="profile">Engineer — payments</p><div class="section"><span '
                        'class="entry-title">Lead — Northwind</span><ul><li>Shipped billing — 40ms</li></ul>'
                        "</div></cv>", "cv")

    assert "Engineer, payments" in body and "Shipped billing, 40ms" in body
    assert "Lead — Northwind" in body


def test_a_letter_has_no_em_dash_and_no_day_it_was_made():
    """No document carries the day it was made (owner, 2026-11-08). A line that is only a date goes; a
    date inside a sentence stays."""
    body = build.answer('<letter><div class="letter"><p>8 November 2026</p><p>Lisbon, 8 November 2026</p>'
                        "<p>Since 8 November 2021 I have run the desk — alone.</p></div></letter>", "cover")

    assert "Since 8 November 2021 I have run the desk, alone." in body
    assert "2026" not in body


# ── the page around it ────────────────────────────────────────────────────────────────────────

def test_the_page_has_their_header_and_the_look_they_picked():
    """Code writes the header: the name and links they typed, the email and phone their CV has."""
    user = _user(links=[{"url": "https://github.com/alice"}, {"url": "alice.dev"}])

    page = build.assemble(_request(user=user, look=KESTREL), build.answer(ANSWER, "cv"))

    assert KESTREL.style in page and MERIDIAN.style not in page
    assert '<div class="cv-name">Alice Example</div>' in page
    assert '<a href="mailto:alice@example.com">alice@example.com</a>' in page
    assert "+49 151 2345 6789" in page
    assert '<a href="https://github.com/alice">github.com/alice</a>' in page
    assert '<a href="https://alice.dev">alice.dev</a>' in page
    assert page.index("cv-header") < page.index('class="profile"')
    # The phone, then the email, then the place: with the place first, a resume parser read no phone
    # number at all ("Greater Manchester 07700 900236", Affinda, 4 of 4 CVs, 2026-11-14).
    contact = page[page.index('class="cv-contact"'):]
    assert contact.index("+49 151 2345 6789") < contact.index("alice@example.com") < contact.index(
        "<span>Berlin, Germany</span>") < contact.index("github.com/alice")


# ── the checks ────────────────────────────────────────────────────────────────────────────────

def test_a_number_found_nowhere_in_their_documents_or_the_posting_is_named():
    sources = "Cut latency to 40ms for 1,200 shops in 2021. The posting wants 5 years."

    assert build.numbers_not_in("40ms, 1200 shops, 2021, 5 years", sources) == ()
    assert build.numbers_not_in("Grew revenue 35% across 1,200 shops, 35% again", sources) == ("35",)


def test_a_document_that_uses_its_whole_ceiling_still_ends_inside_its_calls_clock():
    """The ceiling holds the thinking too, on an API key. At 16,000 the longest CVs the bench wrote would
    have been cut off with nothing to show (22,275 tokens out the longest of 164, 2026-11-15), and a
    ceiling raised past what one call's clock allows would be cut off by the clock instead."""
    from libs.llm.client import TIMEOUT_SEC

    tokens_a_second = 239  # the slowest of claude-haiku-5-5's long CVs at medium (239 to 260 a second)
    assert build.MAX_TOKENS > 22_275
    assert build.MAX_TOKENS / tokens_a_second < TIMEOUT_SEC


def test_the_year_of_a_date_written_with_dots_is_theirs():
    """A driver's licence says "07.02.2029" and his CV "valid to Feb 2029". Read whole, the date is one
    number, so the year was called a number from nowhere and the CV sent back for a second call: 3 of 36
    CVs of two candidate wordings (2026-11-15). A year their documents do not give is still named."""
    sources = "Valid to: 07.02.2029. Érvényes: 2028.05.16. Stock worth 1.200.000 Ft."

    assert build.numbers_not_in("Licence valid to Feb 2029, ADR to May 2028", sources) == ()
    assert build.numbers_not_in("Licence valid to 2030, 200 pallets a day", sources) == ("2030", "200")


def test_a_name_found_nowhere_in_their_documents_or_the_posting_is_named():
    """As a number is: a tool or a method the model brought in itself ("QuickBooks" on the CV of somebody
    who never said it). A word that only starts with a capital is any word, and in German any noun."""
    sources = "Ran Google Ads and Meta Ads with target CPA bidding in GA4. Reported KPIs weekly. HubSpot."

    assert build.names_not_in("Target CPA bidding in GA4, HubSpot and QuickBooks, Google Ads",
                              sources) == ("QuickBooks",)
    assert build.names_not_in("DevOps and MLOps: Docker, CI/CD. Used GA4 and GA5.", sources) == (
        "DevOps", "MLOps", "GA5")
    assert build.names_not_in("Elektroinstallation in Wohn- und Gewerbebauten, 40ms, 10k+, B2B", sources
                              + " B2B") == ()
    assert build.names_not_in("BSc Nursing, three GCSEs", "B.Sc. in Nursing. GCSE English and Maths.") == ()


def test_an_abbreviation_is_allowed():
    """The owner, 2026-11-15: abbreviations are allowed. A CV that said "AGMs" for their "annual general
    meeting", "KPIs" for the figures they report or "CRM" over HubSpot was sent back for a second call: 12
    of 108 CVs written for the panel, and no reviewer counted one as untrue. Capitals alone, or with the "s"
    of a plural, are an abbreviation. A name with a small letter or a figure in it is still checked."""
    sources = "Organised the annual general meeting. Reported cost per lead weekly from HubSpot."

    assert build.names_not_in("Events: AGMs. Report KPIs. CRM and email: HubSpot. ROAS, ÁFA.", sources) == ()
    assert build.names_not_in("CRM: HubSpot and SalesHub, GA4 and B2B", sources) == ("SalesHub", "GA4", "B2B")


def test_a_name_from_nowhere_is_not_said_back_while_the_check_is_out_of_play(monkeypatch):
    """The owner, 2026-11-15: keep the name check, "just in case", and take it out of play. It costs
    nobody a second call now; switched on again it says a name back with the numbers, in a CV written in
    the language of their documents."""
    request = _request()
    named = build.answer(ANSWER.replace("Built the billing service", "Built PayStream into the billing "
                                                                     "service"), "cv")

    assert build.problems(request, named, 1).ok
    assert build.problems(request, '<li>Built PayStream for 35 shops</li>', 1).invented == ("35",)

    monkeypatch.setattr(build, "NAMES_SAID_BACK", True)
    check = build.problems(request, named, 1)
    assert check.invented == ("PayStream",) and not check.ok
    assert ("These are in neither their documents nor the posting: PayStream."
            in build.feedback(request, check, 1))

    # Their documents are English. In a CV written in German for a German posting, a translated name is in
    # neither, and is theirs all the same.
    german = ('<p class="profile">Backend-Entwicklerin mit Erfahrung in der Abrechnung und im Zahlungsverkehr'
              ' '
              'für Onlinehändler in ganz Deutschland, seit vielen Jahren in Berlin tätig.</p>'
              '<div class="section"><div class="section-title">Berufserfahrung</div><ul><li>Verantwortlich '
              "für die Umsatzsteuer-Voranmeldung (UStVA) und die Abrechnung aller Händler</li></ul></div>")
    assert build.problems(request, german, 1).invented == ()


def test_the_checks_count_pages_over_and_numbers_not_theirs():
    request = _request()

    assert build.problems(request, build.answer(ANSWER, "cv"), 2).ok
    over = build.problems(request, '<li>Grew revenue 35%</li>', 3)
    assert over.pages_over == 1 and over.invented == ("35",)
    assert build.problems(_request("cover"), build.answer(LETTER_ANSWER, "cover"), 2).pages_over == 1


def test_what_is_said_back_is_only_what_was_found_and_says_cut_never_shrink():
    """A model told a document is too long will otherwise reduce the font."""
    said = build.feedback(_request(), build.Check(pages_over=1, invented=("35",)), 3)

    assert "3 pages" in said and "fit on 2" in said and "Do not shrink" in said
    assert "These are in neither their documents nor the posting: 35." in said
    assert said.rstrip().endswith("between <cv> and </cv>.")
    assert "numbers" not in build.feedback(_request(), build.Check(pages_over=1, invented=()), 3)


# ── one build, end to end ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_chrome(monkeypatch):
    """Chrome, as a function: what it was handed, how often, and the pages the PDF claims, in order.
    `last` is how many characters every PDF's last page holds."""
    state = {"pages": [1], "calls": 0, "html": [], "last": None}

    def render(html: str) -> bytes:
        state["calls"] += 1
        state["html"].append(html)
        return f"%PDF-1.4 fake {state['calls']}".encode()

    def inspect_pdf(document: bytes, *, kind="cv", headings=()):
        pages = state["pages"]
        return pdf.PdfReport(pages=pages.pop(0) if len(pages) > 1 else pages[0], chars=1_200, issues=[],
                             last_page_chars=state["last"])

    monkeypatch.setattr(run.pdf, "render", render)
    monkeypatch.setattr(run.pdf, "inspect_pdf", inspect_pdf)
    return state


def _session(monkeypatch, *, description="Backend role. Python.", documents=None, user=None):
    """The build's reads (the account, the posting, their documents, their languages) with no database."""
    user = user or _user()
    job = FakeJob(description=description)
    monkeypatch.setattr(run, "_load", lambda _db, _user_id, _job_id: (user, job))
    uploaded = DOCUMENTS if documents is None else documents
    monkeypatch.setattr(run, "documents_of", lambda _db, _user_id: uploaded)
    monkeypatch.setattr(run, "_languages", lambda _db, _user_id: ("en",))
    return object(), user


def _ai(session, user, client=None, **kwargs):
    return run.run_ai(session, user.id, 7, client=client or FakeClient(), model=MODEL,
                      kind=kwargs.pop("kind", "cv"), **kwargs)


def test_a_cv_comes_back_as_a_pdf_named_for_the_person_in_one_call(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch)
    client = FakeClient()

    result = _ai(session, user, client)

    assert result.pdf.startswith(b"%PDF") and result.filename == "Alice_Example_CV.pdf"
    assert result.pages == 1 and result.calls == 1 and not result.revised
    assert fake_chrome["calls"] == 1
    assert client.calls[0]["model"] == MODEL and client.calls[0]["system"] == build.SYSTEM
    assert "Built the billing service" in fake_chrome["html"][0]


def test_a_document_is_kept_by_the_model_that_wrote_it(monkeypatch, fake_chrome):
    """Owner, 2026-11-15. The model that answered, where the provider says: asked for a name, a provider
    answers with the dated model behind it, and a plan may answer with another altogether. Where it does
    not say, the one asked for."""
    from libs.llm.client import Reply

    session, user = _session(monkeypatch)

    assert _ai(session, user, FakeClient(Reply(ANSWER, model="claude-haiku-5-5-20261001"))).model == (
        "claude-haiku-5-5-20261001")
    assert _ai(session, user, FakeClient(ANSWER)).model == MODEL
    # Said back once and the second answer kept: the second answer's model.
    invented = ANSWER.replace("Built the billing service", "Grew revenue 35% with the billing service")
    twice = FakeClient(Reply(invented, model="first"), Reply(ANSWER, model="second"))
    assert _ai(session, user, twice).model == "second"


def test_a_number_they_never_gave_is_said_back_once_and_the_answer_without_it_kept(monkeypatch,
                                                                                     fake_chrome):
    session, user = _session(monkeypatch)
    invented = ANSWER.replace("Built the billing service", "Grew revenue 35% with the billing service")
    client = FakeClient(invented, ANSWER)

    result = _ai(session, user, client)

    assert result.calls == 2 and result.revised
    second = client.calls[1]["messages"]
    assert [message["role"] for message in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == invented
    assert "35" in second[2]["content"] and "pages" not in second[2]["content"]
    assert "35%" not in fake_chrome["html"][-1]
    assert result.pdf == b"%PDF-1.4 fake 2"


def test_a_three_page_cv_is_said_back_once_and_the_one_that_fits_kept(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch)
    fake_chrome["pages"] = [3, 2]

    result = _ai(session, user)

    assert result.calls == 2 and result.pages == 2 and fake_chrome["calls"] == 2


def test_a_second_answer_that_is_worse_is_not_kept(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch)
    fake_chrome["pages"] = [3, 4]

    result = _ai(session, user)

    assert result.calls == 2 and result.pages == 3 and result.pdf == b"%PDF-1.4 fake 1"


def test_a_second_answer_that_never_came_whole_leaves_the_first(monkeypatch, fake_chrome):
    """The first was a CV; a second call that went wrong must not cost the person it."""
    session, user = _session(monkeypatch)
    fake_chrome["pages"] = [3]

    result = _ai(session, user, FakeClient(ANSWER, "<cv><p>cut off"))

    assert result.calls == 2 and result.pdf == b"%PDF-1.4 fake 1" and fake_chrome["calls"] == 1


def test_a_few_lines_on_a_last_page_are_taken_up_by_the_look_and_not_asked_for_again(monkeypatch,
                                                                                     fake_chrome):
    """8 of 36 first drafts cost a second call of ~45 s for one spilled line (2026-11-14)."""
    session, user = _session(monkeypatch)
    fake_chrome["pages"], fake_chrome["last"] = [2, 1], 40
    client = FakeClient()

    result = _ai(session, user, client)

    assert result.calls == 1 and len(client.calls) == 1 and result.pages == 1
    assert fake_chrome["calls"] == 2 and result.pdf == b"%PDF-1.4 fake 2"
    first, smaller = fake_chrome["html"]
    assert "zoom" not in first and smaller.replace("<style>body { zoom: 0.98; }</style>\n", "") == first


def test_lines_no_smaller_drawing_takes_up_are_said_back_as_before(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch)
    fake_chrome["pages"], fake_chrome["last"] = [2], 40
    client = FakeClient()

    result = _ai(session, user, client)

    assert result.calls == 2 and "Only a few lines reach page 2" in client.calls[1]["messages"][2]["content"]
    # Each answer was tried at every smaller drawing, and what they download is the look as designed.
    assert fake_chrome["calls"] == 8 and result.pdf == b"%PDF-1.4 fake 5"
    assert "zoom" not in fake_chrome["html"][4] and "zoom: 0.94" in fake_chrome["html"][3]


def test_a_document_says_how_long_it_took_and_the_tokens_it_used(monkeypatch, fake_chrome, caplog):
    """Owner, 2026-11-14: record the token spend, and time the making. One line a document, with the
    seconds the model wrote for and Chrome drew for, and the tokens of every call it took."""
    from libs.llm.client import Reply, Usage

    session, user = _session(monkeypatch)
    invented = ANSWER.replace("Built the billing service", "Grew revenue 35% with the billing service")
    counted = FakeClient(Reply(invented, Usage(5_000, 2_000)), Reply(ANSWER, Usage(7_400, 1_300)))

    with caplog.at_level("INFO", logger="services.docgen.run"):
        _ai(session, user, counted)
        _ai(session, user, FakeClient())

    built = [record.getMessage() for record in caplog.records if "docgen.built" in record.getMessage()]
    assert "calls=2" in built[0] and "tokens_in=12400 tokens_out=3300" in built[0]
    assert re.search(r"write_s=\d+[.]\d render_s=\d+[.]\d", built[0])
    assert "tokens_in=- tokens_out=-" in built[1], "a tool that does not count is not nought"


def test_a_cv_that_passes_is_never_asked_for_twice(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch)
    fake_chrome["pages"] = [2]

    assert _ai(session, user).calls == 1


def test_a_posting_with_no_description_is_refused_before_anything_is_sent(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch, description="   ")
    client = FakeClient()

    with pytest.raises(run.NotBuildable, match="no description"):
        _ai(session, user, client)
    assert client.calls == [] and fake_chrome["calls"] == 0


def test_a_cover_letter_is_written_in_its_own_look(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch, user=_user(cover_template="tabula"))

    result = _ai(session, user, FakeClient(LETTER_ANSWER), kind="cover")

    page = fake_chrome["html"][0]
    assert result.filename == "Alice_Example_Cover.pdf"
    assert templates.find("cover", "tabula").style in page
    assert "Dear Hiring Manager," in page and "Application for Backend Engineer" not in page


# ── the PDF ───────────────────────────────────────────────────────────────────────────────────

needs_chrome = pytest.mark.skipif(
    not pathlib.Path(pdf.chrome_binary()).exists() and shutil.which(pdf.chrome_binary()) is None,
    reason="no Chrome on this machine (set CHROME_BIN to run it)")

# A whole CV, long enough for the ATS check's floor of 300 characters read.
PAGE = build.assemble(_request(), build.answer(ANSWER.replace(
    "<ul><li>Built the billing service in Python and Postgres</li></ul>",
    "<ul><li>Built the billing service in Python and Postgres, which every shop pays through</li>"
    "<li>Cut checkout latency to 40ms at p99 for 1,200 shops</li>"
    "<li>Took the on-call rota from weekly pages to none in a quarter</li></ul>").replace(
    '<div class="section"><div class="section-title">Education</div></div>',
    '<div class="section"><div class="section-title">Education</div>'
    '<div class="entry"><div class="entry-head">'
    '<span class="entry-title">BSc Computer Science · TU Berlin</span><span class="entry-date">2017</span>'
    "</div></div></div>"), "cv"))


def test_a_browser_that_cannot_start_is_a_compile_failure_not_a_silent_empty_file(monkeypatch):
    monkeypatch.setattr(pdf, "_chrome", None)
    monkeypatch.setattr(pdf, "chrome_binary", lambda: "definitely-not-a-browser")

    with pytest.raises(pdf.CompileFailed):
        pdf.render(PAGE)


@needs_chrome
def test_a_real_chrome_turns_a_written_cv_into_a_pdf_an_ats_reads():
    """The one test that exercises the actual browser, over the pipe a worker keeps it on."""
    report = pdf.inspect_pdf(pdf.render(PAGE))

    assert report.pages == 1
    assert report.ats_ok, report.issues


@needs_chrome
def test_the_pdf_carries_no_browser_header_or_footer():
    """Chrome stamps the date, the address and "1/1" on every page unless told not to."""
    from pypdf import PdfReader

    pages = PdfReader(io.BytesIO(pdf.render(PAGE))).pages
    text = "".join(page.extract_text() for page in pages)

    assert "Alice Example" in text
    assert "about:blank" not in text
    assert "1/1" not in text


@needs_chrome
def test_one_browser_serves_every_document_and_a_dead_one_is_replaced():
    """A browser launched per document was the cost this removes. One that died must not cost the
    next person their document."""
    pdf.render(PAGE)
    first = pdf.browser().process.pid
    pdf.render(PAGE)
    assert pdf.browser().process.pid == first, "the second document launched a second browser"

    pdf.browser().process.kill()
    pdf.browser().process.wait()

    assert pdf.render(PAGE).startswith(b"%PDF")
    assert pdf.browser().process.pid != first


@needs_chrome
def test_chrome_starts_while_the_database_holds_the_descriptors_it_reads_and_writes_on():
    """pymongo closes its sockets in a forked child. When they held 3 and 4, where Chrome reads and writes,
    every launch failed with EBADF (2026-11-11). In a process of its own, so the database connects first
    and its sockets take the lowest descriptors, as in a worker starting up."""
    if not os.environ.get("MONGODB_URI"):
        pytest.skip("needs MONGODB_URI, a local mongod")
    script = ("import os, time, pymongo\n"
              "client = pymongo.MongoClient(os.environ['MONGODB_URI'], serverSelectionTimeoutMS=5000)\n"
              "client.admin.command('ping'); client.test.probe.find_one()\n"
              "held = lambda: [os.readlink(f'/proc/self/fd/{n}')[:7] for n in (3, 4)\n"
              "                if os.path.exists(f'/proc/self/fd/{n}')]\n"
              "deadline = time.monotonic() + 5\n"
              "while held() != ['socket:', 'socket:'] and time.monotonic() < deadline: time.sleep(0.05)\n"
              "print(held())\n"
              "from services.docgen import pdf\n"
              "print(pdf.render('<html><body>hi</body></html>')[:5])\n")

    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120,  # noqa: S603
                          cwd=pathlib.Path(__file__).resolve().parents[1], check=False)

    assert done.stdout.startswith("['socket:', 'socket:']"), f"the premise: {done.stdout}{done.stderr[-500:]}"
    assert "b'%PDF-'" in done.stdout, done.stderr[-2000:]


def test_an_unparseable_pdf_reports_no_page_count_rather_than_raising():
    """`pages=None` is what stops a length retry firing on a file nobody could read."""
    report = pdf.inspect_pdf(b"not a pdf")

    assert report.pages is None
    assert not report.ats_ok


def test_a_cover_letter_is_not_judged_by_the_cvs_checklist():
    """An always-wrong signal is worse than no signal."""
    letter = ("Dear hiring team,\n\nI am writing about the Fullstack Engineer role. "
              "I have spent four years building payment systems in Python and Go, and the "
              "problem you describe is one I have solved before at some length.\n\n"
              "Kind regards,\nAlice Example\n") * 3

    as_cv = pdf.inspect_pdf_text(letter, kind="cv", headings=("Experience", "Education"))
    as_cover = pdf.inspect_pdf_text(letter, kind="cover", headings=("Experience", "Education"))

    assert any("Education" in issue for issue in as_cv.issues), (
        "the CV checklist should still look for its sections")
    assert as_cover.ats_ok, as_cover.issues


# ── what a reviewer would send back (`checks`) ────────────────────────────────────────────────

def _letter(*paragraphs: str) -> str:
    body = "".join(f"<p>{each}</p>" for each in paragraphs)
    return (f'<div class="letter"><p>Dear Hiring Manager,</p>{body}<p class="closing">Kind regards,</p>'
            '<p class="signature">Alice Example</p></div>')


# About two hundred words in short paragraphs, with nothing in it a reviewer would send back.
GOOD_LETTER = _letter(
    "At Northwind I cut checkout latency to 40ms at p99 for 1,200 shops. The Backend Engineer role asks for "
    "the same care with payments, and it is the work I most want to keep doing.",
    "I rebuilt the billing service in Python and Postgres so that refunds settle on the day they are made. "
    "Before the change, support answered the same question about missing money every morning. Afterwards "
    "those tickets stopped, and the finance team closed each month two days earlier.",
    "The latency work started with a slow page that nobody owned. I traced each request through the service, "
    "moved the heaviest query out of the checkout path, and added a cache with a clear owner. The page has "
    "stayed fast since, through two seasonal peaks.",
    "Your posting describes a small team that runs what it builds and is on call for it. That is how I have "
    "worked for four years, and it is why this role appeals to me.",
    "I would like to bring the same habits to your payments platform, starting with the parts that wake "
    "people at night. Quiet nights are the measure I care about most.")


def test_a_cv_that_talks_about_applying_or_rates_itself_is_sent_back_saying_so():
    """Four of the first twelve CVs Haiku 5.5 wrote opened on "applicant" or "applying for the role", and
    three rated themselves ("Strong in"): the line a recruiter's six seconds go on (2026-11-11)."""
    applying = CV_BODY.replace("Backend engineer who", "Backend engineer applying for this role who")
    rating = CV_BODY.replace("who cut", "strong in Python, who cut")

    assert checks.cv(CV_BODY) == [], "a CV with nothing wrong is left alone"
    assert any('"applying"' in fault for fault in checks.cv(applying))
    assert any('"strong in"' in fault for fault in checks.cv(rating))


def _entry(dates: str) -> str:
    return ('<div class="entry"><div class="entry-head"><span class="entry-title">Role</span>'
            f'<span class="entry-date">{dates}</span></div></div>')


def _section(title: str, *entries: str) -> str:
    return f'<div class="section"><div class="section-title">{title}</div>{"".join(entries)}</div>'


def test_a_role_that_ended_above_one_still_held_is_sent_back_and_each_section_is_its_own_list():
    """Eight of twelve CVs put a finished internship above the job the person still holds: the model
    sorted by start date (2026-11-11). Education is a list of its own, so a project after it is not."""
    wrong = _section("Experience", _entry("Jan 2026 - May 2026"), _entry("Jun 2024 - Present"))
    right = (_section("Experience", _entry("Jun 2024 - Present"), _entry("Jan 2026 - May 2026"))
             + _section("Education", _entry("2016 - 2019")) + _section("Projekte", _entry("2025 - heute")))

    assert any("still hold" in fault for fault in checks.cv(wrong))
    assert not any("still hold" in fault for fault in checks.cv(right))
    # A job dated as a chef wrote it, "since March 23", is a job still held (2026-11-14).
    since = _section("Experience", _entry("2021 - Feb 23"), _entry("Since March 23"))
    assert any("still hold" in fault for fault in checks.cv(since))


def test_a_cv_names_nobody_and_states_no_length_of_experience_their_documents_do_not():
    """One profile called its owner "he", a guess from the name, and two worked out "over two years"
    (2026-11-11). A length their own CV gives is theirs to keep."""
    he = CV_BODY.replace("Backend engineer who cut", "Backend engineer. He cut")
    years = CV_BODY.replace("Backend engineer who", "Backend engineer with over two years of work who")

    assert any('"He"' in fault for fault in checks.cv(he))
    assert any("two years" in fault for fault in checks.cv(years, CV_TEXT))
    assert checks.cv(years, CV_TEXT + "\nTwo years of payments work at Northwind") == []


def test_end_to_end_testing_is_work_and_owning_something_end_to_end_is_filler():
    """The old prompt banned "end-to-end", and eleven of those twelve CVs said it, each about Playwright."""
    assert checks.cliches("Built end-to-end testing with Playwright, covering every user flow") == []
    assert checks.cliches("Owned the platform end-to-end") == ["end-to-end"]
    assert checks.cliches("Modelled fee dynamics") == [], "a word that holds a cliché is not one"


def test_dynamic_remarketing_is_a_kind_of_advert_and_a_dynamic_team_is_filler():
    """A marketer's own "dynamic remarketing" was sent back as a word that describes nothing, in 2 of the
    4 CVs written for her (2026-11-14)."""
    assert checks.cliches("Set up dynamic remarketing and catalogue ads from the product feed") == []
    assert checks.cliches("Thrives in a dynamic team") == ["dynamic"]


def test_the_word_rules_are_english_and_a_german_cv_is_held_to_the_rest():
    """A German "dynamisch" is not the cliché the list means; a bullet too long is long in any language."""
    long = ("Entwickelte die Zahlungsplattform für zwölfhundert Händler in Berlin und München neu, "
            "senkte die Latenz an der Kasse deutlich, schrieb die Tests dazu, betreute den Betrieb rund um "
            "die Uhr und "
            "arbeitete eng mit dem Produktteam an neuen Funktionen für Rückerstattungen und Abrechnungen.")
    german = ('<p class="profile">Dynamische Backend-Entwicklerin mit fünf Jahren Erfahrung in Python und '
              'verteilten Systemen.</p><div class="section"><div class="section-title">Berufserfahrung</div>'
              f"<ul><li>{long}</li></ul></div>")

    assert [fault.split()[1:4] for fault in checks.cv(german)] == [["bullet(s)", "run", "over"]]


def test_a_cv_in_another_language_headed_in_english_is_sent_back():
    """One of the eight CVs written in German and Hungarian for the panel was headed "Skills", "Experience",
    "Education" and "Languages", as the prompt's markup example is (2026-11-14)."""
    def german(*headings: str) -> str:
        return ('<p class="profile">Elektroniker für Energie- und Gebäudetechnik mit Erfahrung in der '
                "Elektroinstallation, Wartung und Fehlersuche in Wohn- und Gewerbebauten sowie in "
                "Mietwohnungen im Raum Leipzig.</p>"
                + "".join(_section(heading, "<ul><li>Elektroinstallation in Wohnbauten</li></ul>")
                          for heading in headings))

    said = checks.cv(german("Skills", "Experience", "Education", "Weiterbildung"))
    assert said == ["The headings Skills, Experience, Education are in English and the CV is not. Write "
                    "every heading in the language of the CV."]
    assert checks.cv(german("Kenntnisse", "Berufserfahrung", "Ausbildung")) == []
    assert not any("headings" in fault for fault in checks.cv(CV_BODY)), "an English CV is headed in English"


def test_a_letter_in_another_language_is_measured_by_its_length_on_the_page():
    """Two of four Hungarian letters were sent back as too short at 164 and 166 words, each as long on the
    page as an English one of 230: Hungarian says the same in fewer, longer words (2026-11-14)."""
    hungarian = _letter(
        "Jelenleg 22 ügyfelem, kft.-k, bt.-k és egyéni vállalkozók könyvelését vezetem a Számadó "
        "Könyvelőiroda Kft.-nél, a Northwind könyvelői állására pedig azért jelentkezem, mert ugyanezt a "
        "munkát szeretném egyetlen cégcsoportnál, hosszú távon végezni.",
        "A havi munkám egyik legfontosabb része az áfabevallások elkészítése és beküldése ÁNYK és ONYA "
        "felületen. A bevallásokhoz a számlaadatokat a NAV Online Számla adataival egyeztetem, a "
        "bankkivonatokat pedig naponta könyvelem, hogy a folyószámlák mindig naprakészek legyenek.",
        "Mielőtt könyvelőirodába kerültem, másfél évig a Tölgyes Bútor Kft.-nél dolgoztam pénzügyi "
        "asszisztensként. Itt havonta mintegy 250 szállítói számlát iktattam és rögzítettem Kulcs-Soft "
        "rendszerben, a házipénztárt kezeltem, és a vevői kintlévőségeket figyeltem.",
        "Stabil, magyar tulajdonú családi cégcsoportnál szeretnék hosszú távon dolgozni, és a szakmai "
        "képzések is fontosak számomra. Mérlegképes könyvelő képzésen vagyok, a vizsgám jövő júniusban "
        "várható, addig is a főkönyvelő mellett önállóan vinném a havi feladatok egy részét.",
        "A folyószámla-egyeztetést és a vevő- és szállítóanalitika vezetését jelenleg is önállóan végzem, a "
        "havi zárásokban és az éves beszámolók előkészítésében pedig a vezető könyvelővel dolgozom együtt, "
        "a határidőket minden hónapban pontosan betartva.")
    prose = re.findall(r"<p>(.*?)</p>", hungarian)[1:]
    assert sum(len(part.split()) for part in prose) < checks.LETTER_WORDS[0], "the premise: under 180 words"

    assert not any("words" in fault for fault in checks.letter(hungarian, company="Northwind"))
    # And a letter too short in any language still is.
    short = _letter(*re.findall(r"<p>(.*?)</p>", hungarian)[1:3])
    assert any("words" in fault for fault in checks.letter(short, company="Northwind"))


def test_a_letter_is_sent_back_for_what_gives_a_generated_one_away():
    """A colon in a sentence, an opening that never names the company: what the owner's own review caught
    every time (`libs/scaffolds/tailor_cover.md`, failures 1 and 4). A contraction was the third, and is the
    person's own since 2026-11-27 (the owner, with a reader's word on the letters: "written too correctly or
    not realistic"): people write "I've", and a letter that never does reads as nobody's."""
    bad = (GOOD_LETTER.replace("At Northwind I cut", "In my last role I cut")
           .replace("I rebuilt", "I've rebuilt")
           .replace("so that refunds", "so that one thing held: refunds"))

    assert checks.letter(GOOD_LETTER, company="Northwind GmbH") == []
    assert checks.letter(GOOD_LETTER.replace("I rebuilt", "I've rebuilt"), company="Northwind GmbH") == []
    found = " ".join(checks.letter(bad, company="Northwind GmbH"))
    assert "does not name Northwind" in found and "colon" in found


def test_a_close_that_asks_to_talk_is_the_letters_own_and_a_stock_close_is_sent_back():
    """Owner, 2026-11-27: "the close can happen that's appropriate and guidance expects it". A plain line
    asking to talk was sent back with every other close about applying ("I would welcome a conversation",
    2026-11-11); now only the stock forms are. "A proactive assistant" was A1's own product, in A1's own
    posting, and is theirs to quote."""
    def closing(line: str) -> list[str]:
        return checks.letter(GOOD_LETTER.replace("Quiet nights are the measure I care about most.", line),
                             company="Northwind")

    for asks in ("I would welcome a conversation about the role.", "I would be glad to talk it through.",
                 "I look forward to talking about it.", "I would like to discuss it with you."):
        assert closing(asks) == [], asks
    assert any("thank you for your consideration" in fault for fault in closing(
        "Thank you for your consideration."))
    quoting = GOOD_LETTER.replace("payments platform", "proactive payments platform")

    assert checks.letter(quoting, company="Northwind", posting="We run a proactive payments platform.") == []
    assert checks.letter(quoting, company="Northwind"), "the same word, not the posting's, is a cliché"


def test_a_letter_in_ai_language_is_sent_back_naming_it_and_a_persons_own_words_are_not():
    """Owner, 2026-11-19: "make sure it passes without using any AI language". What current models write many
    times as often as people do (CHANGELOG 2026-11-19, night): Gemini's vocabulary, GPT's and Claude's frames,
    a chatbot's own words. The same things said literally, or quoted from the posting, are the person's."""
    def faults(*changes: tuple[str, str], posting: str = "") -> str:
        letter = GOOD_LETTER
        for old, new in changes:
            letter = letter.replace(old, new)
        return " ".join(checks.letter(letter, company="Northwind", posting=posting))

    gemini = ("The page has stayed fast since", "It is a testament to the team that the page has stayed fast")
    gpt = ("so that refunds settle", "so that refunds settle, rather than simply queue,")
    claude = ("Quiet nights are the measure I care about most.",
              "Billing is not a back office, it is the product.")
    chatbot = ("Quiet nights are the measure I care about most.",
               "Let me know if you would like changes, [Your Name].")
    literal = ("I rebuilt the billing service",
               "I wear a safety harness near load-bearing walls, and I rebuilt the billing service")

    assert "AI" not in faults(), "the letter as a person writes it"
    assert "'testament to'" in faults(gemini) and "reads as written by AI" in faults(gemini)
    assert "'rather than simply'" in faults(gpt)
    assert "'is not a back office, it is'" in faults(claude)
    assert "Let me know if you" in faults(chatbot) and "[Your Name]" in faults(chatbot)
    assert "AI" not in faults(literal), "what the words mean when a person says them"
    posting = "Our uptime is a testament to our engineers."
    assert "AI" not in faults(gemini, posting=posting), "a turn the posting itself uses"



def test_a_letter_names_its_employer_as_people_write_it():
    """A board's company field is not what a person writes: "VNOM | We get the job done", "Bosch Group",
    "J. RETTENMAIER & SÖHNE GmbH + Co KG", "Alloheim Senioren-Residenzen SE". Each letter written for those
    named its employer as the posting does, and was sent back for not naming it: 6 of the 8 so sent back of 36
    letters for people the wording was not tuned on (2026-11-18). A letter that names nobody still is."""
    def opening(text: str) -> str:
        return _letter(text, *GOOD_LETTER.split("</p><p>")[2:4])

    def sent_back(text: str, company: str) -> bool:
        return any("does not name" in fault for fault in checks.letter(opening(text), company=company))

    for company, text in (("VNOM | We get the job done", "Bij VNOM plan ik graag de ritten van morgen."),
                          ("Bosch Group", "I would like to join Bosch Service Solutions in Magdeburg."),
                          ("J. RETTENMAIER & SÖHNE GmbH + Co KG", "Ich möchte zu J. Rettenmaier & Söhne."),
                          ("Rushcliffe Care Limited", "I would like to join Rushcliffe Care Group."),
                          ("Acme Ltd", "I would like to bring that to Acme."),
                          # Written as the company writes itself, in lower case (2026-11-21).
                          ("emagine Polska", "I would like to bring that to emagine as an analyst."),
                          ("dmTECH Polska", "I would like to join dmTECH in Kraków.")):
        assert checks.names_employer(text, company) and not sent_back(text, company), company
    for company, text in (("Alloheim Senioren-Residenzen SE", "Ich möchte in Rheinfelden pflegen."),
                          ("Person Centred Software", "I am a person who keeps a leadership team moving."),
                          ("all inclusive Fitness", "I like an inclusive team on a busy gym floor.")):
        assert not checks.names_employer(text, company) and sent_back(text, company), company
    assert checks.employer("VNOM | We get the job done") == "VNOM"
    assert checks.employer("J. RETTENMAIER & SÖHNE GmbH + Co KG") == "J. RETTENMAIER & SÖHNE"


def test_a_number_written_the_way_another_language_writes_it_is_still_theirs():
    """A Polish CV's "ok. 1 100 osób" is the English letter's "1,100": the check read "1" and "100", and the
    payroll person's letter was sent back for inventing 1,100 under every wording (2026-11-19), a second call
    each. So are a German "1.100" and a decimal comma ("3,5 Jahre", "3.5 years"), in a CV or a letter."""
    for written, sources in (("about 1,100 people a month", "ok. 1 100 osób miesięcznie"),
                             ("1,100 people", "rund 1.100 Beschäftigte"),
                             ("3.5 years", "3,5 Jahre Erfahrung"),
                             ("ok. 1 100 osób", "about 1,100 people"),
                             ("1.250.000 EUR", "1,250,000 EUR"),
                             ("120 clients since 2019", "Since 2019 120 clients")):
        assert build.numbers_not_in(written, sources) == (), written
    # A number they never wrote is still found.
    assert build.numbers_not_in("about 1,200 people", "ok. 1 100 osób") == ("1,200",)


def test_a_letter_names_its_employer_by_the_name_its_posting_uses():
    """Letters for people the wording was not tuned on named their employer as its own posting does, and were
    sent back for it, a second call each (2026-11-19): "1KOMMA5°", a name with figures in it; "AMC" of AMC
    Global Media, three capitals; "JRS", the initials J. Rettenmaier & Söhne goes by in its posting. Initials
    a posting never uses, or a name not written as a name, still do not name the employer."""
    def sent_back(text: str, company: str, posting: str = "") -> bool:
        body = _letter(text, *GOOD_LETTER.split("</p><p>")[2:4])
        faults = checks.letter(body, company=company, posting=posting)
        return any("does not name" in fault for fault in faults)

    rettenmaier = "J. RETTENMAIER & SÖHNE GmbH + Co KG"
    jrs = "Diese Arbeit möchte ich bei JRS in Rosenberg einbringen."
    for company, text, posting in (
            ("1KOMMA5° Commercial Solutions GmbH", "Damit bewerbe ich mich bei 1KOMMA5° als Elektriker.", ""),
            ("AMC Global Media", "Jelentkezem az AMC Media Central Europe koordinátor munkakörére.", ""),
            (rettenmaier, jrs, "JRS wächst in der Region und rund um den Globus.")):
        assert not sent_back(text, company, posting), company
    for company, text, posting in ((rettenmaier, jrs, ""),
                                   ("AMC Global Media", "I would like to join amc in Budapest.", "")):
        assert sent_back(text, company, posting), company

def test_a_paragraph_over_four_sentences_is_cut_where_its_sentences_end_and_not_sent_back():
    """A paragraph over four sentences sent Claude Haiku 5.5's first draft back, a second call on the
    person's plan, 3 of 8 so sent back on the live wording (2026-11-20). It is cut in code, into paragraphs
    of at most three, at its sentences' ends and not at a title's full stop; every word stays."""
    long = ("At Northwind I run the night shift for twelve people. Dr. Baron asked me to train the new "
            "starters. I wrote them a checklist. They learn the floor in a week now. Our lorries leave "
            "on time. The rota is fair to everyone.")
    paragraphs = re.findall(r"<p>(.*?)</p>", GOOD_LETTER)[1:]
    written = _letter(paragraphs[0], long, *paragraphs[2:])

    body = build.answer(f"<letter>{written}</letter>", "cover")

    assert ("At Northwind I run the night shift for twelve people. Dr. Baron asked me to train the new "
            "starters. I wrote them a checklist.</p><p>They learn the floor in a week now.") in body
    assert checks.text(body).split() == checks.text(written).split()
    assert not any("sentences" in fault for fault in checks.letter(body, company="Northwind"))
    assert checks.paragraphs_split(GOOD_LETTER) == GOOD_LETTER, "a letter with nothing to cut is left alone"


def test_a_letter_that_begins_without_a_greeting_is_read_from_its_first_paragraph():
    """Every check skipped a letter's first paragraph, taken for the greeting: a letter that began without
    one had its opening unread, and was sent back for not naming the employer it named there (1 of 144,
    2026-11-20). A greeting is a few words not ended as a sentence."""
    without = GOOD_LETTER.replace("<p>Dear Hiring Manager,</p>", "")

    assert without != GOOD_LETTER
    assert checks.letter(without, company="Northwind") == []
    assert checks.letter_words(without) == checks.letter_words(GOOD_LETTER)
    unnamed = without.replace("At Northwind I cut", "I cut")
    assert any("does not name Northwind" in fault for fault in checks.letter(unnamed, company="Northwind"))


def test_a_closing_paragraph_marked_as_the_sign_off_is_still_read_and_counted():
    """A model marked its closing paragraph `closing`, the class of "Kind regards," alone, and the check
    skipped it: 32 words uncounted, and whatever it said unread, which is where "I look forward to…" is
    written (1 of 54 letters, 2026-11-18). The sign-off is the last paragraph so marked; one length for the
    check and the bench."""
    close = ("I would like to bring the same habits to your payments platform, starting with the parts that "
             "wake people at night. Quiet nights are the measure I care about most.")
    last = ("I would like to bring the same habits to your payments platform. Thank you for your "
            "consideration.")
    marked = GOOD_LETTER.replace(f"<p>{close}</p>", f'<p class="closing">{last}</p>')
    shorter_by = len(close.split()) - len(last.split())

    assert marked != GOOD_LETTER
    assert any("thank you for your consideration" in fault
               for fault in checks.letter(marked, company="Northwind"))
    assert checks.letter_words(marked) == checks.letter_words(GOOD_LETTER) - shorter_by
    # Greeting, sign-off and name are not the letter's length.
    assert checks.letter_words(GOOD_LETTER) == sum(len(each.split()) for each in re.findall(
        r"<p>(.*?)</p>", GOOD_LETTER)[1:])


def test_a_second_page_with_a_few_lines_on_it_is_sent_back_and_a_full_one_is_not():
    """The fourth failure the owner's review caught every time: an orphan line or two on page two. Said
    back only when no smaller drawing of the look took the lines up (`pdf.fitted`)."""
    request, body = _request(), build.answer(ANSWER, "cv")
    few, full = (pdf.PdfReport(pages=2, chars=3_000, last_page_chars=last) for last in (120, 2_400))

    assert few.spilled and not full.spilled
    assert not pdf.PdfReport(pages=1, chars=3_000, last_page_chars=120).spilled
    assert any("page 2" in fault for fault in build.problems(request, body, 2, few.spilled).faults)
    assert build.problems(request, body, 2, full.spilled).ok


def test_a_line_that_says_only_what_they_did_is_not_sent_back(monkeypatch, fake_chrome):
    """Owner, 2026-11-14. "Helped the sous chef with orders" is all a chef's documents say of it, and it was
    said back as "describes a task" for 8 of 44 CVs. Four of those second calls were run: each reworded
    the line, and one made it "Shared ordering". The CV goes out as written, in one call."""
    session, user = _session(monkeypatch)
    helped = ANSWER.replace("Built the billing service in Python and Postgres",
                            "Helped the lead engineer with the billing service in Python and Postgres")
    client = FakeClient(helped)

    result = _ai(session, user, client)

    assert checks.cv(build.answer(helped, "cv")) == []
    assert result.calls == 1 and len(client.calls) == 1


def test_a_fault_is_said_back_once_and_the_answer_without_it_kept(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch)
    applying = ANSWER.replace("Backend engineer who", "Backend engineer applying for this role who")
    client = FakeClient(applying, ANSWER)

    result = _ai(session, user, client)

    assert result.calls == 2 and result.revised
    assert '"applying"' in client.calls[1]["messages"][2]["content"]
    assert "applying" not in fake_chrome["html"][-1]
    assert client.calls[0]["effort"] == build.EFFORT, "every call says how hard to think"


def test_a_profile_written_as_several_paragraphs_is_laid_out_as_one():
    body = build.answer('<cv><p class="profile">Backend engineer.</p>\n<p class="profile">Cut latency '
                        'to 40ms.</p><div class="section"><div class="section-title">Experience</div>'
                        "</div></cv>", "cv")

    assert body.count('class="profile"') == 1 and "Backend engineer. Cut latency to 40ms." in body


def test_the_ats_check_reads_a_cv_by_its_own_headings_in_any_language():
    """It looked for "experience" and "education", which a CV written in German never says."""
    text = ("Alice Example\nalice@example.com | Berlin\nBerufserfahrung\n"
            + "Backend-Entwicklerin bei Northwind, Zahlungsplattform in Python und Postgres\n" * 4
            + "Ausbildung\nInformatik, Technische Universität Berlin\n")

    assert pdf.inspect_pdf_text(text, headings=("Berufserfahrung", "Ausbildung")).ats_ok
    assert pdf.inspect_pdf_text(text, headings=("Ausbildung", "Berufserfahrung")).issues, "in order"
