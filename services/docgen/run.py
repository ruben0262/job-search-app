"""One `docgen` queue job, end to end: tailor, compile, check, deliver, record, discard.

Split from `__main__` for the same reason `services/notifier/deliver.py` is: the transport — a
Telegram upload — is one injected function, so the whole sequence can be driven by a test with a
fake client and a fake Chrome, and a Telegram API change touches one place.

**The order is the design.** Deliver first, then record. A crash between them costs a duplicate
delivery on retry, which is a user receiving their CV twice; the reverse order costs a build that
was recorded and never arrived, which is a user who thinks they applied and did not. When one of
them has to be wrong, it is the recoverable one.

**Documents are ephemeral** (D9). The PDF exists in a scratch directory for the length of one
upload and the directory is removed in a `finally` — no object storage, no retention policy, and
nobody's CV on this disk after the tap that asked for it. `UserJobState.applied_at` plus the pinned
corpus row is the durable record of what was built and for which posting.
"""
from __future__ import annotations

import logging
import pathlib
import re
import shutil
import tempfile
import uuid as _uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select

from libs.contracts.profile import ProfileSchema
from libs.corpus.repository import CorpusRepository
from libs.db.models import CorpusJob, User, UserJobState, UserLabel
from libs.obs import audit
from libs.profiles import ProfileIncomplete
from libs.profiles.repository import ProfileFiles
from services.docgen import build, pdf
from services.docgen.refresh import PostingClosed, refresh_before_build

logger = logging.getLogger(__name__)

# What the file is called when it lands in the user's chat. Their own name, because a recruiter
# receiving `cv.pdf` from three candidates has three files called `cv.pdf`.
FILENAME_SUFFIX = "CV"

# One retry, and only for length. A second call is the user's money, so it is spent on the one
# failure that is both common and mechanically detectable.
MAX_TAILOR_CALLS = 2


class NotBuildable(RuntimeError):
    """This account or this job cannot produce a CV, and retrying will not change that."""


@dataclass(frozen=True)
class BuildResult:
    job_id: int
    pages: int | None
    calls: int
    ats_ok: bool
    delivered_to: str


def run_build(session, user_id: _uuid.UUID, job_id: int, *,
              client,
              send_document: Callable[..., None],
              token: str,
              kind: str = "cv",
              http: object | None = None,
              workdir: pathlib.Path | None = None) -> BuildResult:
    """Build one tailored CV for one posting and deliver it.

    **Does not commit** — the worker owns the transaction, so the record of the build and the
    queue job's completion land together or neither does (`libs/queue/worker.py`).

    `http` is the user's own HTTP client, used for the pre-build freshness check. Optional so the
    tests and any caller without a proxy lease can skip it; when it is passed, a posting that has
    closed stops the build before a token is spent.
    """
    if kind not in ("cv", "cover"):
        raise NotBuildable(f"unknown document kind {kind!r}")
    user, job, score = _load(session, user_id, job_id)

    # **Before anything is spent.** A CV costs the user real money, and the worst possible outcome
    # is that all of it succeeds against a listing that closed three days ago. Only a positive
    # "closed" stops the build — a failed fetch proceeds, because a proxy hiccup must not present
    # to the user as "that job is gone".
    if http is not None:
        try:
            refresh_before_build(session, job, http)
        except PostingClosed as exc:
            raise NotBuildable(str(exc)) from exc
    profile = ProfileSchema.model_validate(user.profile)

    # **The reference instance's `CLAUDE.md` and skill files, per user** (`libs/profiles`). This is
    # the port of `cwd=REPO_ROOT`: what the CLI loads off disk automatically, assembled explicitly.
    # `ProfileIncomplete` here means setup was never finished — terminal, not a retry.
    files = ProfileFiles(session, user_id)
    try:
        grounding = files.grounding()
    except ProfileIncomplete as exc:
        raise NotBuildable(str(exc)) from exc

    # Their own CV, with their real experience in it — written by the setup agent into
    # `base_cv.html`, not rendered from a schema.
    base_cv = files.read("base_cv.html")
    if not base_cv or not base_cv.strip():
        raise NotBuildable("this account has no base CV yet — finish setup first")

    scratch = pathlib.Path(workdir) if workdir else pathlib.Path(tempfile.mkdtemp(prefix="docgen-"))
    try:
        context = build.job_context(job, score=score,
                                    fit_notes=_fit_notes(session, user_id, job_id),
                                    salary_bands=_salary_bands(user))

        if kind == "cover":
            html, pages, calls = _cover(client, profile, context, base_cv=base_cv,
                                        grounding=grounding, scratch=scratch,
                                        template=files.read("tailor_cover.md"))
        else:
            html, pages, calls = _tailor_until_it_fits(
                client, profile, context, base_cv=base_cv, facts_md=grounding, scratch=scratch,
                template=files.read("tailor_cv.md"),
                role_angles=files.read("08-role-angles.md"))

        leftover = build.scan_hidden(html)
        if leftover:
            # Fail closed. A CV with invisible keyword stuffing in it looks perfect and gets the
            # person filtered out — or blacklisted — by the system it was aimed at, and they would
            # never learn why. Not delivering is the recoverable outcome.
            raise NotBuildable(f"the generated CV contained hidden content: {'; '.join(leftover)}")

        source = scratch / f"{kind}.html"
        source.write_text(html, encoding="utf-8")
        document = pdf.compile_pdf(source, scratch / _filename(profile, kind))
        report = pdf.inspect_pdf(document)

        # **The personal chat, never a feed channel.** The channels may have other members in them,
        # and this document is the most personal artifact the platform produces.
        chat_id = user.telegram_chat_id
        if not chat_id:
            raise NotBuildable("this account has no personal Telegram chat to deliver to")

        send_document(token, chat_id, document.read_bytes(), document.name,
                      _caption(job))
        _record(session, user_id, job)

        if not report.ats_ok:
            # A warning rather than a refusal: the layout is single-column and known good, so this
            # catches the unexpected. Refusing to deliver over it would be the worse failure.
            logger.warning("docgen: ATS check flagged %s for user %s: %s",
                           job.url, user_id, report.issues)

        audit(logger, "docgen.built", user=user_id, job=job_id, pages=report.pages,
              calls=calls, ats_ok=report.ats_ok, chars=report.chars)
        return BuildResult(job_id=job_id, pages=pages, calls=calls, ats_ok=report.ats_ok,
                           delivered_to=chat_id)
    finally:
        # D9. Runs on every path including the failures — a build that crashed mid-compile has left
        # a half-written CV on disk, and that is exactly the file that must not survive.
        shutil.rmtree(scratch, ignore_errors=True)


def _cover(client, profile: ProfileSchema, context: str, *,
           base_cv: str, grounding: str | None, scratch: pathlib.Path,
           template: str | None = None) -> tuple[str, int | None, int]:
    """One call, no revision loop.

    A cover letter has a word count in its own prompt and a mechanical scan that enforces it, so the
    page-count feedback the CV needs would be spending a second call to fix a rule the model already
    has. The letter is built from the *base* CV rather than a tailored one for the same reason the
    reference's `build_cover_only` does: the tailored copy is ephemeral and rebuilding it would
    double the cost of pressing one button.
    """
    letter = build.tailor_cover(client, profile, base_cv, context, facts_md=grounding,
                                template=template)
    return letter, _page_count(letter, scratch), 1


def _tailor_until_it_fits(client, profile: ProfileSchema, context: str, *,
                          base_cv: str, facts_md: str | None, scratch: pathlib.Path,
                          template: str | None = None,
                          role_angles: str | None = None) -> tuple[str, int | None, int]:
    """Tailor, compile, and if the PDF is too long, say so once and tailor again.

    The compile is what makes this loop honest: the page count is measured from the real PDF rather
    than estimated from the markup, because the model's own sense of how long its output will print
    is not evidence of anything.
    """
    base = base_cv

    html = build.tailor(client, profile, base, context, facts_md=facts_md, template=template,
                        role_angles=role_angles)
    pages = _page_count(html, scratch)

    feedback = build.page_feedback(pages)
    if feedback is None:
        return html, pages, 1

    logger.info("docgen: %s pages, asking for one revision", pages)
    retried = build.tailor(client, profile, base, context, facts_md=facts_md,
                           template=template, role_angles=role_angles, feedback=feedback)
    retried_pages = _page_count(retried, scratch)

    # Keep whichever is shorter. A revision that came back *longer* is not an improvement, and the
    # user has already paid for both.
    if retried_pages is not None and (pages is None or retried_pages < pages):
        return retried, retried_pages, 2
    return html, pages, 2


def _page_count(html: str, scratch: pathlib.Path) -> int | None:
    """Compile to a throwaway PDF just to count the pages. `None` when it could not be measured."""
    probe = scratch / "probe.html"
    probe.write_text(html, encoding="utf-8")
    try:
        return pdf.inspect_pdf(pdf.compile_pdf(probe, scratch / "probe.pdf")).pages
    except pdf.CompileFailed:
        # Let the real compile raise. Failing here would turn a transient Chrome problem into a
        # dead-lettered build before the document was even finished.
        logger.warning("docgen: the page probe did not compile; skipping the length check")
        return None


def _fit_notes(session, user_id: _uuid.UUID, job_id: int) -> str | None:
    """What the labeller concluded about this job, in its own fifteen words.

    The angle the CV is written toward. Without it the model has a description and a CV and has to
    infer the connection; with it, it starts from the one this platform already drew.
    """
    return session.scalar(
        select(UserLabel.rationale)
        .where(UserLabel.user_id == user_id, UserLabel.job_id == job_id))


def _load(session, user_id: _uuid.UUID, job_id: int) -> tuple[User, CorpusJob, float | None]:
    """Everything the build needs, with the reasons it cannot run stated as refusals.

    All four are terminal rather than retryable. A missing profile does not appear on a retry, and
    a build that keeps trying is a queue job that occupies a slot and then dead-letters anyway,
    ten minutes of Chrome later.
    """
    user = session.get(User, user_id)
    if user is None:
        raise NotBuildable(f"no such user: {user_id}")
    if not user.profile:
        raise NotBuildable("this account has no confirmed profile — finish onboarding first")

    job = session.get(CorpusJob, job_id)
    if job is None:
        raise NotBuildable(f"job {job_id} is no longer in the corpus")
    if not (job.description or "").strip():
        # Without the posting there is nothing to tailor *to*, and a CV tailored to a job title is
        # the generic CV the user already has.
        raise NotBuildable("this posting has no description stored — nothing to tailor against")

    state = session.get(UserJobState, (user_id, job_id))
    return user, job, (state.score if state else None)


def _salary_bands(user: User) -> dict | None:
    """This user's target ask bands, from their config.

    Per user, where the reference instance had one set in a config file — the whole point of the
    🎯 line is that it is *their* ask, and a shared default would understate somebody.
    """
    config = getattr(user, "config", None)
    return (getattr(config, "config", None) or {}).get("salary_bands") if config else None


def _record(session, user_id: _uuid.UUID, job: CorpusJob) -> None:
    """The durable half of an ephemeral document.

    `applied_at` is set by the first build and never moved — the same rule the feedback path
    follows, and the reason the column is described as the row a user would be angriest to lose.
    Building a CV for a posting is the act of applying to it in every way that matters here.

    The corpus row is pinned in the same breath, so retention cannot remove the description out
    from under a record that says the user applied to it.
    """
    state = session.get(UserJobState, (user_id, job.id))
    if state is None:
        state = UserJobState(user_id=user_id, job_id=job.id)
        session.add(state)
    if state.applied_at is None:
        state.applied_at = func.now()

    CorpusRepository(session).pin(job.id)
    session.flush()


def _filename(profile: ProfileSchema, kind: str = "cv") -> str:
    """`Alice_Example_CV.pdf`. Their name, because a recruiter receiving `cv.pdf` from three
    candidates has three files called `cv.pdf`."""
    label = "Cover" if kind == "cover" else FILENAME_SUFFIX
    slug = re.sub(r"[^A-Za-z0-9]+", "_", profile.identity.name).strip("_")
    return f"{slug}_{label}.pdf" if slug else f"{label}.pdf"


def _caption(job: CorpusJob) -> str:
    company = f" · {job.company}" if job.company else ""
    return f"{job.title}{company}\n{job.url}"
