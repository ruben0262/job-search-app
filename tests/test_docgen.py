"""The CV builder: what reaches the model, what comes back, and what is never delivered."""
from __future__ import annotations

import io
import pathlib
import shutil
import uuid

import pytest

from libs.templates import SCAFFOLD_CV, load
from services.docgen import build, pdf, run


def base_document() -> str:
    """The document handed to the tailoring call.

    The scaffold a new account is seeded with, which is what every user's own `base_cv.html`
    descends from — and since 2026-08-14 the only base CV in the repo.
    """
    return load(SCAFFOLD_CV)


# The invisible character these tests are about, named and escaped.
ZERO_WIDTH = "\u200b"

TAILORED = """<!DOCTYPE html>
<html lang="en"><head><title>Alice Example CV</title></head>
<body>
<div class="cv-header"><div class="cv-name">Alice Example</div></div>
<p class="profile">Senior backend engineer &mdash; payments and APIs.</p>
<div class="section"><div class="section-title">Experience</div>
  <div class="entry"><div class="entry-head">
    <span class="entry-title">Backend Engineer &mdash; Northwind</span></div>
    <ul><li>Shipped the billing service &mdash; 40ms p99</li></ul></div>
</div>
</body></html>"""


# The per-user writing prompt as `setup-prompts` leaves it: the person's real details in the file.
# Every build needs one now. `_require_personalised` refuses a scaffold rather than substituting into
# it, because the substitution read the wrong copy of the candidate — one account's three typed links
# were a single link in its profile, and another's name lost its accent. Tests that relied on the
# scaffold fallback pass this instead, which is what a real account has.
TAILOR_PROMPT = """You are tailoring Alice Example's CV for ONE specific job.
Contact: alice@example.com | https://github.com/alice
Write in British English. Alice may work in the UK and the EU.

<<ROLE_ANGLES>>

<JOB_CONTEXT>

<BASE_TEMPLATE>
"""

COVER_PROMPT = """Write a one-page cover letter for Alice Example, based in Berlin, Germany.

<HEAD_BLOCK>

<JOB_CONTEXT>
"""


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
        self.posted_at = None
        self.applicants = 25
        self.workplace = "remote"
        self.application_url = None


class FakeClient:
    """Records what it was asked, answers with whatever the test queued."""

    def __init__(self, *replies):
        self.replies = list(replies) or [TAILORED]
        self.calls: list[dict] = []

    def complete_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def complete_json(self, **kwargs):  # pragma: no cover - docgen never takes this path
        raise AssertionError("docgen must ask for text, not JSON")


# ── what reaches the model ────────────────────────────────────────────────────────────────────

def test_the_call_is_grounded_on_the_user_s_own_facts():
    """The reference instance measured what happens without grounding."""
    client = FakeClient()
    build.tailor(client, base_document(),
                 build.job_context(FakeJob()), facts_md="# Facts about Alice Example",
                 template=TAILOR_PROMPT, model=MODEL)

    assert client.calls[0]["system"].startswith("# Facts about Alice Example")


def test_a_profile_with_no_facts_block_still_gets_a_grounding_instruction():
    """Nobody should reach this — a confirmed profile always renders one."""
    client = FakeClient()
    build.tailor(client, base_document(), build.job_context(FakeJob()), template=TAILOR_PROMPT, model=MODEL)

    assert "Never invent" in client.calls[0]["system"]


def test_the_prompt_carries_the_candidate_the_posting_and_the_base_document():
    prompt = build.build_prompt(base_document(),
                                build.job_context(FakeJob()), template=TAILOR_PROMPT)

    assert "Alice Example" in prompt              # filled by render_cv_prompt
    assert "Northwind" in prompt                  # the base document
    assert "on-call" in prompt                    # the posting
    assert "<JOB_CONTEXT>" not in prompt and "<BASE_TEMPLATE>" not in prompt


def test_the_model_never_sees_the_stylesheet():
    """It is told CSS is managed externally."""
    client = FakeClient()
    build.tailor(client, base_document(), build.job_context(FakeJob()), template=TAILOR_PROMPT, model=MODEL)

    assert "@page" not in client.calls[0]["messages"][0]["content"]


def test_a_long_posting_is_capped_not_dropped():
    context = build.job_context(FakeJob(description="x" * (build.JD_MAX_CHARS + 5_000)))

    assert "truncated" in context
    assert len(context) < build.JD_MAX_CHARS + 500


def test_a_posting_with_no_description_still_produces_a_context():
    """A build refuses earlier when the description is missing."""
    assert "no description" in build.job_context(FakeJob(description=None))


# ── what comes back ───────────────────────────────────────────────────────────────────────────

def test_the_layout_is_restored_from_the_template_whatever_came_back():
    """The one guarantee that makes 'the model only changes text' true rather than requested."""
    client = FakeClient(TAILORED.replace("<head>", "<head><style>body{font-size:4pt}</style>"))
    html = build.tailor(client, base_document(),
                        build.job_context(FakeJob()), template=TAILOR_PROMPT, model=MODEL)

    assert "@page" in html and "size: A4" in html
    assert "font-size:4pt" not in html


def test_commentary_around_the_document_is_discarded():
    client = FakeClient(f"Here is the tailored CV:\n\n{TAILORED}\n\nLet me know if…")
    html = build.tailor(client, base_document(),
                        build.job_context(FakeJob()), template=TAILOR_PROMPT, model=MODEL)

    assert html.lstrip().startswith("<!DOCTYPE html")
    assert "Let me know" not in html


def test_a_reply_with_no_document_is_a_build_failure():
    client = FakeClient("I cannot help with that.")

    with pytest.raises(build.BuildFailed):
        build.tailor(client, base_document(), build.job_context(FakeJob()), template=TAILOR_PROMPT,
                     model=MODEL)


def test_em_dashes_are_stripped_from_prose_and_kept_in_entry_titles():
    """The prompt bans them in bullets and allows them in a title header."""
    html = build.sanitise(TAILORED)

    assert "Shipped the billing service, 40ms p99" in html
    assert "Backend Engineer &mdash; Northwind" in html


def test_hidden_content_is_stripped_and_then_verified():
    dirty = TAILORED.replace("<li>", '<li style="display:none">').replace(
        "Northwind", "North\u200bwind")

    cleaned = build.sanitise(dirty)

    assert build.scan_hidden(cleaned) == []
    assert "display:none" not in cleaned
    assert "\u200b" not in cleaned


def test_the_scan_reports_what_it_found():
    issues = build.scan_hidden('<p>a\u200bb</p><span style="opacity:0">keywords</span>')

    assert any("invisible" in issue for issue in issues)
    assert any("hiding style" in issue for issue in issues)


def test_the_stylesheet_is_not_scanned_as_hidden_content():
    """`hidden-scrollbars` style rules and print CSS are the platform's own and legitimate."""
    assert build.scan_hidden(base_document()) == []


def test_a_base_cv_that_kept_the_scaffold_comment_reaches_the_model_whole_and_comes_back_clean():
    """The scaffold's comment says "Keep the <style> block intact", before the real `<head>`.

    The stylesheet was lifted with a search that started at those words, so it took the end of the
    comment, `<head>` and `<title>[YOUR NAME] – CV</title>` along with the CSS. The model was sent a
    base CV with no `<body>`, and the CV it wrote came back with the placeholder title restored into
    it — which the placeholder check refuses to deliver, for every account that kept the comment.
    """
    client = FakeClient()
    html = build.tailor(client, base_document(), build.job_context(FakeJob()),
                        template=TAILOR_PROMPT, model=MODEL)

    assert "<body" in client.calls[0]["messages"][0]["content"], "the model never saw the body"
    assert "[YOUR NAME]" not in html
    assert html.count("<title>") == 1


# ── the length rule ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("pages", "wanted"), [(1, False), (2, False), (3, True), (None, False)])
def test_only_a_cv_over_two_pages_asks_for_a_revision(pages, wanted):
    """One page is the goal, two is the ceiling."""
    assert (build.page_feedback(pages) is not None) is wanted


def test_the_revision_says_cut_never_shrink():
    """A model told a document is too long will otherwise reduce the font."""
    feedback = build.page_feedback(3)

    assert "Cut" in feedback
    assert "Do not shrink" in feedback


# ── the PDF checks ────────────────────────────────────────────────────────────────────────────

needs_chrome = pytest.mark.skipif(
    not pathlib.Path(pdf.chrome_binary()).exists() and shutil.which(pdf.chrome_binary()) is None,
    reason="no Chrome on this machine (set CHROME_BIN to run it)")


def test_a_browser_that_cannot_start_is_a_compile_failure_not_a_silent_empty_file(monkeypatch):
    monkeypatch.setattr(pdf, "_chrome", None)
    monkeypatch.setattr(pdf, "chrome_binary", lambda: "definitely-not-a-browser")

    with pytest.raises(pdf.CompileFailed):
        pdf.render(TAILORED)


@needs_chrome
def test_a_real_chrome_turns_the_rendered_cv_into_a_readable_pdf():
    """The one test that exercises the actual browser, over the pipe a worker keeps it on."""
    html = build.tailor(FakeClient(), base_document(), build.job_context(FakeJob()),
                        template=TAILOR_PROMPT, model=MODEL)

    report = pdf.inspect_pdf(pdf.render(html))

    assert report.pages == 1
    # Extractable text is the whole question: a PDF Chrome rasterised without fonts.
    assert report.chars > 100, f"no extractable text: {report.issues}"


@needs_chrome
def test_the_pdf_carries_no_browser_header_or_footer():
    """Chrome stamps the date, the address and "1/1" on every page unless told not to."""
    from pypdf import PdfReader

    pages = PdfReader(io.BytesIO(pdf.render(TAILORED))).pages
    text = "".join(page.extract_text() for page in pages)

    assert "Alice Example" in text
    assert "about:blank" not in text
    assert "1/1" not in text


@needs_chrome
def test_one_browser_serves_every_document_and_a_dead_one_is_replaced(monkeypatch):
    """A browser launched per document was the cost this removes. One that died must not cost the
    next person their document."""
    monkeypatch.setattr(pdf, "_chrome", None)
    pdf.render(TAILORED)
    first = pdf.browser().process.pid
    pdf.render(TAILORED)
    assert pdf.browser().process.pid == first, "the second document launched a second browser"

    pdf.browser().process.kill()
    pdf.browser().process.wait()

    assert pdf.render(TAILORED).startswith(b"%PDF")
    assert pdf.browser().process.pid != first


def test_an_unparseable_pdf_reports_no_page_count_rather_than_raising():
    """`pages=None` is what stops a length retry firing on a file nobody could read."""
    report = pdf.inspect_pdf(b"not a pdf")

    assert report.pages is None
    assert not report.ats_ok


# ── one build, end to end ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_chrome(monkeypatch):
    """Chrome, as a function: what it was handed, how often, and the pages the PDF claims."""
    state = {"pages": 1, "calls": 0, "html": ""}

    def render(html: str) -> bytes:
        state["calls"] += 1
        state["html"] = html
        return b"%PDF-1.4 fake"

    def inspect_pdf(document: bytes, *, kind="cv"):
        return pdf.PdfReport(pages=state["pages"], chars=1_200, issues=[])

    monkeypatch.setattr(run.pdf, "render", render)
    monkeypatch.setattr(run.pdf, "inspect_pdf", inspect_pdf)
    return state


# A finished account: grounding, a base CV, **and both writing prompts personalised**. The last two
# were absent, so every build in these tests fell through to the shipped scaffold and had five markers
# substituted from the schema. That path is gone — a scaffold is now refused — so the fixture carries
# what a real account carries after `setup-prompts` has run.
DEFAULT_FILES = {"grounding": "# Facts about Alice Example", "base_cv.html": TAILORED,
                 "tailor_cv.md": TAILOR_PROMPT, "tailor_cover.md": COVER_PROMPT}

# An uploaded CV as text, the way `libs.documents.extract` leaves one.
CV_TEXT = """Alice Example
alice@example.com | Berlin, Germany

EXPERIENCE
Backend Engineer, Northwind    2021 - Present
- Built the billing service in Python and Postgres
- Cut checkout latency to 40ms at p99

EDUCATION
BSc Computer Science, TU Berlin    2017
"""


def _session(monkeypatch, *, description="Backend role. Python.",
             profile_files=DEFAULT_FILES, documents=None):
    """The build's two reads — the account and the posting — and its config read, with no database."""
    # `full_name` is where the document's filename comes from — the name the user typed at signup.
    # The profile schema was a second copy of the same fact; nothing reads it, and it is retired.
    user = type("U", (), {"id": uuid.uuid4(), "full_name": "Alice Example", "links": [],
                          "facts_md": "# Facts about Alice Example"})()
    job = FakeJob(description=description)
    state = type("S", (), {"applied_at": None})()
    monkeypatch.setattr(run, "_load", lambda _db, _user_id, _job_id: (user, job))
    monkeypatch.setattr(run, "_salary_bands", lambda _db, _user_id: None)

    # The profile is files now (`libs/profiles`), not a schema render.
    class Files:
        def __init__(self, *_args):
            pass

        def grounding(self):
            if profile_files is None:
                raise run.ProfileIncomplete("no completed profile")
            return profile_files["grounding"]

        def read(self, name):
            return (profile_files or {}).get(name)

    monkeypatch.setattr(run, "ProfileFiles", Files)
    # Their uploads by kind, as `libs.suggestions.documents_of` reads them.
    monkeypatch.setattr(run, "documents_of", lambda _session, _user_id: documents or {})
    return object(), user, job, state


def _ai(session, user, client=None, **kwargs):
    return run.run_ai(session, user.id, 7, client=client or FakeClient(), model=MODEL,
                      kind=kwargs.pop("kind", "cv"), **kwargs)


def test_an_ai_cv_comes_back_as_a_pdf_named_for_the_person(monkeypatch, fake_chrome):
    session, user, _job, _state = _session(monkeypatch)

    result = _ai(session, user)

    assert result.pdf.startswith(b"%PDF")
    assert result.filename == "Alice_Example_CV.pdf"
    assert result.pages == 1 and result.calls == 1


def test_the_model_writes_on_the_model_its_payer_resolved_to(monkeypatch, fake_chrome):
    """A Gemini key must not be sent a Claude model's name, nor the other way round."""
    session, user, _job, _state = _session(monkeypatch)
    client = FakeClient()

    run.run_ai(session, user.id, 7, client=client, model="gemini-3.8-flash", kind="cover")

    assert [call["model"] for call in client.calls] == ["gemini-3.8-flash"]


def test_a_three_page_cv_is_revised_once(monkeypatch, fake_chrome):
    session, user, _job, _state = _session(monkeypatch)
    fake_chrome["pages"] = 3
    client = FakeClient(TAILORED, TAILORED)

    result = _ai(session, user, client)

    assert result.calls == 2
    assert "REVISION NEEDED" in client.calls[1]["messages"][0]["content"]


def test_a_cv_that_already_fits_is_not_revised(monkeypatch, fake_chrome):
    """A second call is the user's money."""
    session, user, _job, _state = _session(monkeypatch)
    client = FakeClient()

    result = _ai(session, user, client)

    assert result.calls == 1 and len(client.calls) == 1


def test_a_cv_with_hidden_content_is_never_delivered(monkeypatch, fake_chrome):
    """Fail closed."""
    session, user, _job, state = _session(monkeypatch)
    # Zero-width characters inside a *tag name* survive the sanitiser's attribute-level cleanup.
    sneaky = TAILORED.replace('<p class="profile">',
                              f'<p class="profile" data-x="{ZERO_WIDTH}">')
    monkeypatch.setattr(build, "strip_hidden", lambda html: html)

    with pytest.raises(run.NotBuildable):
        _ai(session, user, FakeClient(sneaky))

    assert fake_chrome["calls"] == 0
    assert state.applied_at is None


def test_an_account_with_no_completed_profile_refuses_before_spending_a_call(monkeypatch,
                                                                            fake_chrome):
    """Setup was never finished."""
    session, user, _job, _state = _session(monkeypatch, profile_files=None)
    client = FakeClient()

    with pytest.raises(run.NotBuildable):
        _ai(session, user, client)

    assert client.calls == [], "a call was spent on an account that could never receive a CV"


def test_an_account_with_no_base_cv_refuses(monkeypatch, fake_chrome):
    """The base CV is the document the tailoring prompt edits."""
    session, user, _job, _state = _session(
        monkeypatch, profile_files={"grounding": "# Facts", "base_cv.html": ""})

    with pytest.raises(run.NotBuildable):
        _ai(session, user)


def test_a_posting_with_no_description_is_refused(monkeypatch, fake_chrome):
    """A CV tailored to a job title is the generic CV the user already has."""
    session, user, _job, _state = _session(monkeypatch, description="")

    with pytest.raises(run.NotBuildable):
        _ai(session, user)


# ── the instant document ──────────────────────────────────────────────────────────────────────

def test_an_instant_cv_is_their_own_lines_and_spends_no_model_call(monkeypatch, fake_chrome):
    """Free for everyone: built by `libs.documents.instant` from the CV they uploaded."""
    session, user, _job, state = _session(monkeypatch, documents={"cv": CV_TEXT})

    result = run.run_instant(session, user.id, 7, kind="cv")

    assert "Built the billing service in Python and Postgres" in fake_chrome["html"]
    assert result.pdf.startswith(b"%PDF") and result.filename == "Alice_Example_CV.pdf"
    assert result.calls == 0
    # A free preview, not an application: only "I applied" on the jobs page records one.
    assert state.applied_at is None


def test_an_instant_cover_letter_is_a_letter(monkeypatch, fake_chrome):
    """The kind asked for is the kind built."""
    session, user, _job, _state = _session(monkeypatch, documents={"cv": CV_TEXT})

    result = run.run_instant(session, user.id, 7, kind="cover")

    assert "Dear Hiring Manager" in fake_chrome["html"]
    assert result.filename == "Alice_Example_Cover.pdf"


def test_an_instant_document_without_their_cv_says_what_to_do(monkeypatch, fake_chrome):
    """A CV built from nothing is a page with a name on it, delivered as if it were a CV."""
    session, user, _job, _state = _session(monkeypatch, documents={"diploma": "BSc, 2017"})

    with pytest.raises(run.NotBuildable, match="upload your CV"):
        run.run_instant(session, user.id, 7, kind="cv")

    assert fake_chrome["calls"] == 0


# ── what the model is told about the posting ──────────────────────────────────────────────────

def test_the_context_carries_the_scoring_time_assessment():
    """Trimming this made the CVs worse."""
    context = build.job_context(FakeJob(), score=86,
                                fit_notes="Python API role, matches her backend work.")

    assert "What we understand" in context
    assert "matches her backend work" in context


def test_the_context_carries_the_money_and_the_competition():
    """Both salary lines answer different questions — what the role pays."""
    job = FakeJob(description="Salary 60,000 - 80,000 EUR per annum. Python.")
    context = build.job_context(job, score=86)

    # The en dash is the formatter's own, lifted with lib/salary.py.
    assert f"Posted salary: 60k{chr(0x2013)}80k EUR/yr" in context
    assert "Target ask band:" in context
    assert "Applicants: 25" in context
    assert "Workplace: remote" in context


def test_a_posting_with_no_salary_says_nothing_rather_than_guessing():
    context = build.job_context(FakeJob(description="Python role. No pay stated."), score=70)

    assert "Posted salary" not in context
    # The ask band is the user's own and stands whether or not the posting states one.
    assert "Target ask band:" in context


def test_the_token_ceiling_leaves_room_for_the_document_after_thinking():
    """A ceiling the thinking budget eats is a failed build with no explanation."""
    from services.docgen.build import MAX_TOKENS, THINKING_TOKENS

    # A CV document, generously.
    document_tokens = 8_000
    assert document_tokens * 2 <= MAX_TOKENS - THINKING_TOKENS


def test_a_cover_letter_is_not_judged_by_the_cvs_checklist(tmp_path):
    """An always-wrong signal is worse than no signal."""
    letter = ("Dear hiring team,\n\nI am writing about the Fullstack Engineer role. "
              "I have spent four years building payment systems in Python and Go, and the "
              "problem you describe is one I have solved before at some length.\n\n"
              "Kind regards,\nAlice Example\n") * 3

    as_cv = pdf.inspect_pdf_text(letter, kind="cv")
    as_cover = pdf.inspect_pdf_text(letter, kind="cover")

    assert any("education" in issue for issue in as_cv.issues), (
        "the CV checklist should still look for sections")
    assert as_cover.ats_ok, as_cover.issues


# ── one render per document (2026-08-07) ──────────────────────────────────────────────────────

def test_a_cv_that_fits_is_rendered_once(monkeypatch, fake_chrome):
    """The delivered PDF is the one that was measured: there is no separate probe render."""
    session, user, _job, _state = _session(monkeypatch)

    result = _ai(session, user)

    assert fake_chrome["calls"] == 1, "the delivered PDF is the one that was measured"
    assert result.calls == 1
    assert result.revised is False


def test_a_cv_that_came_back_too_long_is_rendered_twice_and_says_so(monkeypatch, fake_chrome):
    """The revision loop is unchanged — but each one costs a second call and a second render."""
    session, user, _job, _state = _session(monkeypatch)
    fake_chrome["pages"] = 4

    result = _ai(session, user)

    assert fake_chrome["calls"] == 2, "one render per attempt, and no separate probe"
    assert result.calls == 2
    assert result.revised is True, "the rate of this is what says whether the prompt needs work"


def test_the_downloaded_file_is_named_for_the_person_after_a_revision_too(monkeypatch, fake_chrome):
    """The browser shows the filename, and "retry-Alice_CV.pdf" is not a CV."""
    session, user, _job, _state = _session(monkeypatch)
    fake_chrome["pages"] = 4

    assert _ai(session, user).filename == "Alice_Example_CV.pdf"


def test_a_document_that_fails_its_checks_never_reaches_chrome(monkeypatch, fake_chrome):
    """A check answerable from the markup must not cost a render.

    `sanitise` strips hidden content on the way out of the model and `scan_hidden` verifies the
    strip worked, so the two agreeing is the normal case — this forces the disagreement to assert
    the ordering: the scan runs first, and a document that fails it is refused before any render.
    """
    session, user, _job, _state = _session(monkeypatch)
    monkeypatch.setattr(run.build, "scan_hidden", lambda _html: ["display:none in the header"])

    with pytest.raises(run.NotBuildable, match="hidden content"):
        _ai(session, user)

    assert fake_chrome["calls"] == 0


def test_the_prompt_is_told_what_day_it_is():
    """A cover letter carries a date, and nothing was supplying one.

    The model wrote one from its own sense of time: a letter generated in August 2026 went out
    dated 22 June 2026. It is the first line an employer reads, on a document nobody proofreads
    before it is sent.
    """
    from datetime import UTC, datetime

    context = build.job_context(FakeJob())

    assert datetime.now(UTC).strftime("%d %B %Y") in context


def test_the_date_reaches_the_cover_prompt_itself():
    """Asserted through the prompt the model is handed, not just the context helper."""
    from datetime import UTC, datetime

    client = FakeClient(TAILORED)
    build.tailor_cover(client, base_document(), build.job_context(FakeJob()),
                       template=COVER_PROMPT, model=MODEL)

    sent = client.calls[0]["messages"][0]["content"]
    assert datetime.now(UTC).strftime("%d %B %Y") in sent


def test_the_cover_prompt_is_not_handed_a_text_dump_of_the_cv():
    """The CV reaches the cover call as grounding, never as a plain-text extract in the prompt.

    It used to be both. The system prompt is the account's whole profile — tens of thousands of
    characters, verbatim — and the user prompt *also* carried 1,500 characters of tag-stripped
    `base_cv.html` under a heading announcing itself as the candidate's CV and telling the model
    not to copy it wholesale. Measured over 8 postings on 2026-08-09, the model copied out of it in
    five letters of eight; with the extract gone, one of eight (t=-2.38).

    The `<head>` is still read from the same document, because that is what carries the fonts and
    the page rules, so this asserts the *prose* is absent rather than that the CV is unused.
    """
    # A CV with a short head, so its body sits inside the 1,500 characters the extract used to
    # take. The shipped template's head is longer than that on its own, which is worth recording:
    # against a real CV the extract carried nothing the `<head>` substitution did not already
    # carry, and was pure token cost.
    cv = ("<html><head><style>p{margin:0}</style></head><body>"
          "<div class='cv-name'>Alice Example</div>"
          "<p>Rebuilt the settlement ledger so month-end close ran in nine minutes.</p>"
          "<p>Led the migration off the vendor billing platform without a customer-visible gap.</p>"
          "</body></html>")
    sentinel = "Rebuilt the settlement ledger so month-end close ran in nine minutes."

    client = FakeClient(TAILORED)
    build.tailor_cover(client, cv, build.job_context(FakeJob()),
                       facts_md="the candidate's grounding block", template=COVER_PROMPT, model=MODEL)

    sent = client.calls[0]["messages"][0]["content"]
    assert sentinel not in sent, "a text extract of the CV reached the cover prompt"
    assert "<CV_HTML>" not in sent, "the marker was left unfilled in the prompt"


def test_the_cover_prompt_still_carries_the_page_rules():
    """The other half: dropping the extract must not drop the styling the letter is laid out with."""
    client = FakeClient(TAILORED)
    cv = base_document()
    build.tailor_cover(client, cv, build.job_context(FakeJob()), template=COVER_PROMPT, model=MODEL)

    sent = client.calls[0]["messages"][0]["content"]
    assert build.head_block(cv) in sent


# ── what must never reach a person ────────────────────────────────────────────────────────────

def test_a_document_that_came_back_carrying_a_placeholder_is_never_delivered(monkeypatch,
                                                                            fake_chrome):
    """The prompt is checked before the call; this checks what came back.

    `build._require_personalised` refuses a writing prompt that still carries a marker, so a model
    handed a real prompt has none to echo. Nothing checked the other end. A CV addressed to
    `<<NAME>>`, or one whose footer reads `<<CONTACT>>`, would have compiled, been delivered, and
    gone to an employer — no exception, no failed job, just a worse document than the person
    thinks they sent. That is the failure class this whole week has been about: silent, and visible
    only as quality.
    """
    session, user, _job, _state = _session(monkeypatch)
    leaked = TAILORED.replace("Alice Example", "<<NAME>>")

    with pytest.raises(run.NotBuildable, match="placeholder"):
        _ai(session, user, FakeClient(leaked, leaked))

    assert fake_chrome["calls"] == 0, "nothing was rendered, so nothing could be downloaded"
