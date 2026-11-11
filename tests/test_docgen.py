"""The document writer: what reaches the model, what is kept of its answer, and what is never delivered."""
from __future__ import annotations

import io
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import pytest

from libs import templates
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


def test_the_answer_is_the_last_complete_one_and_nothing_around_it():
    """A model that works aloud may write the tag in its working before its answer."""
    reply = (f'Plan: the <cv> should lead with billing.\n<cv><p class="profile">Draft</p></cv>\n'
             f"Then:\n{ANSWER}\nDone.")

    assert build.answer(reply, "cv").startswith('<p class="profile">Backend engineer')


@pytest.mark.parametrize("reply", ["", "Here is the CV.", "<cv><p>cut off", "<cv>   </cv>"])
def test_a_reply_with_no_complete_answer_is_a_build_failure(reply):
    with pytest.raises(build.BuildFailed):
        build.draft(FakeClient(reply), _request(), model=MODEL)


def test_em_dashes_leave_a_cvs_sentences_and_stay_in_its_titles():
    body = build.answer('<cv><p class="profile">Engineer — payments</p><span class="entry-title">Lead — '
                        'Northwind</span><ul><li>Shipped billing — 40ms</li></ul></cv>', "cv")

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


def test_a_letter_names_the_company_and_its_place_above_the_subject():
    body = build.assemble(_request("cover"), build.answer(LETTER_ANSWER, "cover")).split("<body>", 1)[1]

    assert '<p class="letter-to">Northwind<br>Berlin</p>' in body
    assert body.index("letter-to") < body.index("letter-subject") < body.index('class="letter"')


def test_a_letter_for_a_job_with_no_company_or_place_has_no_block_for_them():
    job = FakeJob()
    job.company, job.location = None, None

    page = build.assemble(_request("cover", job=job), build.answer(LETTER_ANSWER, "cover"))

    assert "letter-meta" not in page.split("<body>", 1)[1]


# ── the checks ────────────────────────────────────────────────────────────────────────────────

def test_a_number_found_nowhere_in_their_documents_or_the_posting_is_named():
    sources = "Cut latency to 40ms for 1,200 shops in 2021. The posting wants 5 years."

    assert build.numbers_not_in("40ms, 1200 shops, 2021, 5 years", sources) == ()
    assert build.numbers_not_in("Grew revenue 35% across 1,200 shops, 35% again", sources) == ("35",)


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
    assert "These numbers are not in their documents or the posting: 35." in said
    assert said.rstrip().endswith("between <cv> and </cv>.")
    assert "numbers" not in build.feedback(_request(), build.Check(pages_over=1, invented=()), 3)


# ── one build, end to end ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_chrome(monkeypatch):
    """Chrome, as a function: what it was handed, how often, and the pages the PDF claims, in order."""
    state = {"pages": [1], "calls": 0, "html": []}

    def render(html: str) -> bytes:
        state["calls"] += 1
        state["html"].append(html)
        return f"%PDF-1.4 fake {state['calls']}".encode()

    def inspect_pdf(document: bytes, *, kind="cv", headings=()):
        pages = state["pages"]
        return pdf.PdfReport(pages=pages.pop(0) if len(pages) > 1 else pages[0], chars=1_200, issues=[])

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


def test_a_cover_letter_is_written_in_its_own_look_with_the_company_above_it(monkeypatch, fake_chrome):
    session, user = _session(monkeypatch, user=_user(cover_template="tabula"))

    result = _ai(session, user, FakeClient(LETTER_ANSWER), kind="cover")

    page = fake_chrome["html"][0]
    assert result.filename == "Alice_Example_Cover.pdf"
    assert templates.find("cover", "tabula").style in page
    assert '<p class="letter-to">Northwind<br>Berlin</p>' in page
    assert "Application for Backend Engineer" in page


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
def test_one_browser_serves_every_document_and_a_dead_one_is_replaced(monkeypatch):
    """A browser launched per document was the cost this removes. One that died must not cost the
    next person their document."""
    monkeypatch.setattr(pdf, "_chrome", None)
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
    return ('<p class="letter-subject">Backend engineer specialising in payments</p><div class="letter">'
            f'<p>Dear Hiring Manager,</p>{body}<p class="closing">Kind regards,</p>'
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


def test_a_letter_is_sent_back_for_what_gives_a_generated_one_away():
    """A colon in a sentence, a contraction, an opening that never names the company: what the owner's own
    review caught every time (`libs/scaffolds/tailor_cover.md`, failures 1, 4 and 5)."""
    bad = (GOOD_LETTER.replace("At Northwind I cut", "In my last role I cut")
           .replace("I rebuilt", "I've rebuilt")
           .replace("so that refunds", "so that one thing held: refunds"))

    assert checks.letter(GOOD_LETTER, company="Northwind GmbH") == []
    found = " ".join(checks.letter(bad, company="Northwind GmbH"))
    assert "does not name Northwind" in found and "colon" in found and "I've" in found


def test_a_letter_that_closes_on_applying_is_sent_back_and_one_quoting_the_posting_is_not():
    """Every letter the first wording wrote closed on "I would welcome a conversation", and "a proactive
    assistant" was A1's own product, in A1's own posting (2026-11-11)."""
    welcome = GOOD_LETTER.replace("Quiet nights are the measure I care about most.",
                                  "I would welcome a conversation about the role.")
    quoting = GOOD_LETTER.replace("payments platform", "proactive payments platform")

    assert any("i would welcome" in fault for fault in checks.letter(welcome, company="Northwind"))
    assert checks.letter(quoting, company="Northwind", posting="We run a proactive payments platform.") == []
    assert checks.letter(quoting, company="Northwind"), "the same word, not the posting's, is a cliché"


def test_a_second_page_with_a_few_lines_on_it_is_sent_back_and_a_full_one_is_not():
    """The fourth failure the owner's review caught every time: an orphan line or two on page two."""
    request, body = _request(), build.answer(ANSWER, "cv")

    assert any("page 2" in fault for fault in build.problems(request, body, 2, 120).faults)
    assert build.problems(request, body, 2, 2_400).ok


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
