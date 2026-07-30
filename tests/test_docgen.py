"""The CV builder: what reaches the model, what comes back, and what is never delivered.

Chrome and Claude are both faked here, and that is the point of how `services/docgen` is split —
`build.py` takes strings and returns strings, `pdf.py` is the only thing that shells out, and
`run.py` sequences them. So the rules that matter can be tested without a browser, without a
credential, and without spending anybody's money:

- the model is grounded on the user's own facts block, and never sees the platform's CSS;
- the layout comes back from the template regardless of what was returned;
- a document with hidden content is not delivered at all;
- the PDF is deleted whether the build succeeded or failed.
"""
from __future__ import annotations

import pathlib
import shutil
import uuid

import pytest

from libs.contracts.profile import ProfileSchema
from services.docgen import build, pdf, run

PROFILE = ProfileSchema.model_validate({
    "identity": {"name": "Alice Example", "location": "Berlin, Germany",
                 "links": {"portfolio": "https://alice.dev"}},
    "experience": [{"role": "Backend Engineer", "org": "Northwind", "start": "2021",
                    "end": "present", "bullets": ["Built the billing service"],
                    "stack": ["Python"]}],
    "skills": {"languages": ["Python", "Go"]},
    "targets": {"role_types": ["backend engineer"], "seniority": "Senior"},
    "constraints": {"languages": ["English (Native)"]},
    "confirmed": True,
})

# The invisible character these tests are about, named and escaped — a literal one in a source line
# is precisely what nobody can review, which is the point of the check it exercises.
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


class FakeJob:
    """The corpus row a build reads, without a database.

    Carries the posting metadata the card and the job context both read — a build that only sees a
    title and a description writes a document that matches keywords rather than one written toward
    an angle (`REBUILD_PLAN.md`).
    """

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
    """The reference instance measured what happens without grounding: the model invented an
    employer and fabricated metrics. In a hosted product that lands on a real application."""
    client = FakeClient()
    build.tailor(client, PROFILE, build.base_document(PROFILE),
                 build.job_context(FakeJob()), facts_md="# Facts about Alice Example")

    assert client.calls[0]["system"].startswith("# Facts about Alice Example")


def test_a_profile_with_no_facts_block_still_gets_a_grounding_instruction():
    """Nobody should reach this — a confirmed profile always renders one — but an empty system
    prompt would be the one call where the model is free to invent."""
    client = FakeClient()
    build.tailor(client, PROFILE, build.base_document(PROFILE), build.job_context(FakeJob()))

    assert "Never invent" in client.calls[0]["system"]


def test_the_prompt_carries_the_candidate_the_posting_and_the_base_document():
    prompt = build.build_prompt(PROFILE, build.base_document(PROFILE),
                                build.job_context(FakeJob()))

    assert "Alice Example" in prompt              # filled by render_cv_prompt
    assert "Northwind" in prompt                  # the base document
    assert "on-call" in prompt                    # the posting
    assert "<JOB_CONTEXT>" not in prompt and "<BASE_TEMPLATE>" not in prompt


def test_the_model_never_sees_the_stylesheet():
    """It is told CSS is managed externally. Sending it anyway costs ~700 tokens each way and
    invites an edit that is then silently discarded."""
    client = FakeClient()
    build.tailor(client, PROFILE, build.base_document(PROFILE), build.job_context(FakeJob()))

    assert "@page" not in client.calls[0]["messages"][0]["content"]


def test_a_long_posting_is_capped_not_dropped():
    context = build.job_context(FakeJob(description="x" * (build.JD_MAX_CHARS + 5_000)))

    assert "truncated" in context
    assert len(context) < build.JD_MAX_CHARS + 500


def test_a_posting_with_no_description_still_produces_a_context():
    """A build refuses earlier when the description is missing, but this must not raise on the way
    to that refusal."""
    assert "no description" in build.job_context(FakeJob(description=None))


# ── what comes back ───────────────────────────────────────────────────────────────────────────

def test_the_layout_is_restored_from_the_template_whatever_came_back():
    """The one guarantee that makes 'the model only changes text' true rather than requested."""
    client = FakeClient(TAILORED.replace("<head>", "<head><style>body{font-size:4pt}</style>"))
    html = build.tailor(client, PROFILE, build.base_document(PROFILE),
                        build.job_context(FakeJob()))

    assert "@page" in html and "size: A4" in html
    assert "font-size:4pt" not in html


def test_commentary_around_the_document_is_discarded():
    client = FakeClient(f"Here is the tailored CV:\n\n{TAILORED}\n\nLet me know if…")
    html = build.tailor(client, PROFILE, build.base_document(PROFILE),
                        build.job_context(FakeJob()))

    assert html.lstrip().startswith("<!DOCTYPE html")
    assert "Let me know" not in html


def test_a_reply_with_no_document_is_a_build_failure():
    client = FakeClient("I cannot help with that.")

    with pytest.raises(build.BuildFailed):
        build.tailor(client, PROFILE, build.base_document(PROFILE), build.job_context(FakeJob()))


def test_em_dashes_are_stripped_from_prose_and_kept_in_entry_titles():
    """The prompt bans them in bullets and allows them in a title header. The model ignores that
    intermittently because em-dashes are its habit, so it is enforced rather than asked."""
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
    assert build.scan_hidden(build.template_base()) == []


# ── the length rule ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("pages", "wanted"), [(1, False), (2, False), (3, True), (None, False)])
def test_only_a_cv_over_two_pages_asks_for_a_revision(pages, wanted):
    """One page is the goal, two is the ceiling. An unknown page count must not spend a second call
    — 'we could not count them' is not evidence that there are too many."""
    assert (build.page_feedback(pages) is not None) is wanted


def test_the_revision_says_cut_never_shrink():
    """A model told a document is too long will otherwise reduce the font, which produces a CV that
    fits and cannot be read — and the CSS is restored from the template anyway, so the attempt is
    silently undone and the length unchanged."""
    feedback = build.page_feedback(3)

    assert "Cut" in feedback
    assert "Do not shrink" in feedback


# ── the PDF checks ────────────────────────────────────────────────────────────────────────────

def test_a_missing_pdf_is_a_compile_failure_not_a_silent_empty_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "chrome_binary", lambda: "definitely-not-a-browser")
    source = tmp_path / "cv.html"
    source.write_text(TAILORED, encoding="utf-8")

    with pytest.raises(pdf.CompileFailed):
        pdf.compile_pdf(source, tmp_path / "cv.pdf")


@pytest.mark.skipif(not pathlib.Path(pdf.chrome_binary()).exists()
                    and shutil.which(pdf.chrome_binary()) is None,
                    reason="no Chrome on this machine (set CHROME_BIN to run it)")
def test_a_real_chrome_turns_the_rendered_cv_into_a_readable_pdf(tmp_path):
    """The one test that exercises the actual browser. Skipped where there is none, and the docgen
    image is built with one — so this is the gate that says the layout survives a real render
    rather than that the strings were assembled correctly."""
    source = tmp_path / "cv.html"
    source.write_text(build.base_document(PROFILE), encoding="utf-8")

    report = pdf.inspect_pdf(pdf.compile_pdf(source, tmp_path / "cv.pdf"))

    assert report.pages == 1
    assert report.chars > 100


def test_an_unparseable_pdf_reports_no_page_count_rather_than_raising(tmp_path):
    """`pages=None` is what stops a length retry firing on a file nobody could read."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    report = pdf.inspect_pdf(broken)

    assert report.pages is None
    assert not report.ats_ok


# ── one build, end to end ─────────────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self):
        self.sent: list[tuple] = []

    def __call__(self, token, chat_id, content, filename, caption=""):
        self.sent.append((token, chat_id, content, filename, caption))


@pytest.fixture
def fake_chrome(monkeypatch):
    """Chrome, as a function that writes a file. `pages` is what the fake PDF claims to have."""
    state = {"pages": 1, "calls": 0}

    def compile_pdf(source: pathlib.Path, output: pathlib.Path):
        state["calls"] += 1
        output.write_bytes(b"%PDF-1.4 fake")
        return output

    def inspect_pdf(path):
        return pdf.PdfReport(pages=state["pages"], chars=1_200, issues=[])

    monkeypatch.setattr(run.pdf, "compile_pdf", compile_pdf)
    monkeypatch.setattr(run.pdf, "inspect_pdf", inspect_pdf)
    return state


DEFAULT_FILES = {"grounding": "# Facts about Alice Example", "base_cv.html": TAILORED}


def _session(monkeypatch, *, profile=PROFILE, chat_id="4242", description="Backend role. Python.",
             profile_files=DEFAULT_FILES):
    """A stand-in for the two `session.get` calls and the pin, with no database."""
    user = type("U", (), {"id": uuid.uuid4(), "profile": profile.model_dump(),
                          "facts_md": "# Facts about Alice Example",
                          "profile_context": None, "config": None,
                          "telegram_chat_id": chat_id})()
    job = FakeJob(description=description)
    state = type("S", (), {"score": 88.0, "applied_at": None})()
    pinned: list[int] = []

    class Session:
        def scalar(self, _statement):
            """The labeller's fifteen-word read on this job, which the build writes toward."""
            return "Backend API role; matches her Python and Postgres work."

        def get(self, model, key, **kwargs):
            name = model.__name__
            if name == "User":
                return user
            if name == "CorpusJob":
                return job
            return state

        def add(self, _obj):  # pragma: no cover - the state row exists in these tests
            pass

        def flush(self):
            pass

    monkeypatch.setattr(run, "CorpusRepository",
                        lambda _s: type("R", (), {"pin": lambda _self, job_id: pinned.append(job_id)})())

    # The profile is files now (`libs/profiles`), not a schema render. Stubbed here so these tests
    # stay about docgen: what reaches the model, what comes back, and what is never delivered.
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
    # `func.now()` is a SQL expression, not a value; nothing here evaluates it.
    return Session(), user, job, state, pinned


def test_a_build_delivers_the_pdf_to_the_personal_chat(monkeypatch, fake_chrome, tmp_path):
    """**Never a feed channel.** Those have other members in them, and this is the most personal
    artifact the platform produces."""
    session, user, _job, _state, _pinned = _session(monkeypatch)
    send = Recorder()

    result = run.run_build(session, user.id, 7, client=FakeClient(),
                           send_document=send, token="bot-token", workdir=tmp_path)

    (_token, chat_id, content, filename, caption) = send.sent[0]
    assert chat_id == "4242"
    assert content.startswith(b"%PDF")
    assert filename == "Alice_Example_CV.pdf"
    assert "Northwind" in caption
    assert result.pages == 1 and result.calls == 1


def test_the_build_records_that_the_user_applied_and_pins_the_posting(monkeypatch, fake_chrome,
                                                                     tmp_path):
    """An ephemeral document needs a durable record, and retention must not remove the description
    out from under it."""
    session, user, _job, state, pinned = _session(monkeypatch)

    run.run_build(session, user.id, 7, client=FakeClient(), send_document=Recorder(),
                  token="t", workdir=tmp_path)

    assert state.applied_at is not None
    assert pinned == [7]


def test_a_three_page_cv_is_revised_once(monkeypatch, fake_chrome, tmp_path):
    session, user, _job, _state, _pinned = _session(monkeypatch)
    fake_chrome["pages"] = 3
    client = FakeClient(TAILORED, TAILORED)

    result = run.run_build(session, user.id, 7, client=client, send_document=Recorder(),
                           token="t", workdir=tmp_path)

    assert result.calls == 2
    assert "REVISION NEEDED" in client.calls[1]["messages"][0]["content"]


def test_a_cv_that_already_fits_is_not_revised(monkeypatch, fake_chrome, tmp_path):
    """A second call is the user's money."""
    session, user, _job, _state, _pinned = _session(monkeypatch)
    client = FakeClient()

    result = run.run_build(session, user.id, 7, client=client, send_document=Recorder(),
                           token="t", workdir=tmp_path)

    assert result.calls == 1 and len(client.calls) == 1


def test_a_cv_with_hidden_content_is_never_delivered(monkeypatch, fake_chrome, tmp_path):
    """Fail closed. Invisible keyword stuffing looks perfect and gets the person filtered out by
    the system it was aimed at, and they would never learn why."""
    session, user, _job, state, _pinned = _session(monkeypatch)
    # Zero-width characters inside a *tag name* survive the sanitiser's attribute-level cleanup.
    sneaky = TAILORED.replace('<p class="profile">',
                              f'<p class="profile" data-x="{ZERO_WIDTH}">')
    monkeypatch.setattr(build, "strip_hidden", lambda html: html)
    send = Recorder()

    with pytest.raises(run.NotBuildable):
        run.run_build(session, user.id, 7, client=FakeClient(sneaky), send_document=send,
                      token="t", workdir=tmp_path)

    assert send.sent == []
    assert state.applied_at is None


def test_an_account_with_no_completed_profile_refuses_before_spending_a_call(monkeypatch,
                                                                            fake_chrome, tmp_path):
    """Setup was never finished. Terminal, and refused before the credential is touched — a call
    spent on an account that cannot receive a document is money for nothing."""
    session, user, _job, _state, _pinned = _session(monkeypatch, profile_files=None)
    client = FakeClient()

    with pytest.raises(run.NotBuildable):
        run.run_build(session, user.id, 7, client=client, send_document=Recorder(),
                      token="t", workdir=tmp_path)

    assert client.calls == [], "a call was spent on an account that could never receive a CV"


def test_an_account_with_no_base_cv_refuses(monkeypatch, fake_chrome, tmp_path):
    """The base CV is the document the tailoring prompt edits. Without one there is nothing to
    tailor, and the model would invent a CV from the grounding alone."""
    session, user, _job, _state, _pinned = _session(
        monkeypatch, profile_files={"grounding": "# Facts", "base_cv.html": ""})

    with pytest.raises(run.NotBuildable):
        run.run_build(session, user.id, 7, client=FakeClient(), send_document=Recorder(),
                      token="t", workdir=tmp_path)


def test_an_account_with_no_telegram_chat_refuses(monkeypatch, fake_chrome, tmp_path):
    session, user, _job, _state, _pinned = _session(monkeypatch, chat_id=None)

    with pytest.raises(run.NotBuildable):
        run.run_build(session, user.id, 7, client=FakeClient(), send_document=Recorder(),
                      token="t", workdir=tmp_path)


def test_a_posting_with_no_description_is_refused(monkeypatch, fake_chrome, tmp_path):
    """A CV tailored to a job title is the generic CV the user already has."""
    session, user, _job, _state, _pinned = _session(monkeypatch, description="")

    with pytest.raises(run.NotBuildable):
        run.run_build(session, user.id, 7, client=FakeClient(), send_document=Recorder(),
                      token="t", workdir=tmp_path)


def test_the_documents_do_not_outlive_the_build(monkeypatch, fake_chrome, tmp_path):
    """D9, and the reason this service needs no volume: a scratch directory for the length of one
    upload, removed on every path."""
    session, user, _job, _state, _pinned = _session(monkeypatch)
    scratch = tmp_path / "build"
    scratch.mkdir()

    run.run_build(session, user.id, 7, client=FakeClient(), send_document=Recorder(),
                  token="t", workdir=scratch)

    assert not scratch.exists()


def test_the_documents_do_not_survive_a_failure_either(monkeypatch, fake_chrome, tmp_path):
    """The half-written CV of a crashed build is exactly the file that must not stay on disk."""
    session, user, _job, _state, _pinned = _session(monkeypatch)
    scratch = tmp_path / "build"
    scratch.mkdir()

    def explode(*_args, **_kwargs):
        raise RuntimeError("telegram is down")

    with pytest.raises(RuntimeError):
        run.run_build(session, user.id, 7, client=FakeClient(), send_document=explode,
                      token="t", workdir=scratch)

    assert not scratch.exists()


# ── what the model is told about the posting ──────────────────────────────────────────────────

def test_the_context_carries_the_scoring_time_assessment():
    """**Trimming this made the CVs worse.** A model given only a description matches keywords
    against it; one told what we already concluded about the role has an angle to write toward.
    The labeller stores that read in `user_labels.rationale` and nothing used it until now."""
    context = build.job_context(FakeJob(), score=86,
                                fit_notes="Python API role, matches her backend work.")

    assert "What we understand" in context
    assert "matches her backend work" in context


def test_the_context_carries_the_money_and_the_competition():
    """Both salary lines answer different questions — what the role pays, and what she should be
    asking for. And an opening written for a job with 200 applicants is not the one written for a
    job with six."""
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
    """**A ceiling the thinking budget eats is a failed build with no explanation.**

    Observed 2026-07-30: `stop_reason=max_tokens` with zero text blocks — the reasoning finished
    and the document had not started. The budget is spent inside the ceiling, and a CV is itself
    around 6,000 tokens of HTML, so the margin has to be real rather than nominal.
    """
    from services.docgen.build import MAX_TOKENS, THINKING_TOKENS

    # A CV document, generously. If the ceiling is not several times this after thinking is paid
    # for, a long CV truncates and the user gets nothing.
    document_tokens = 8_000
    assert document_tokens * 2 <= MAX_TOKENS - THINKING_TOKENS
