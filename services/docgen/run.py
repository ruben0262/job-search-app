"""One `docgen` build, end to end: write the document, check it, render it once, record it.

Two ways to write it. **Instant** is code: free for everyone, from the person's own uploads
(`libs.documents.instant`). **AI** is the tailoring prompts, unchanged, on whichever key
`libs.llm.resolve` chose, for an account with the personalised setup files those prompts need. Either
way the PDF comes back as bytes for the worker to keep until it is downloaded (D9).
"""
from __future__ import annotations

import logging
import re
import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from libs.db.models import CorpusJob, User, UserJobState, UserLabel
from libs.documents import instant
from libs.obs import audit
from libs.profiles import PLACEHOLDER_MARKERS, ProfileIncomplete
from libs.profiles.repository import ProfileFiles
from libs.suggestions import documents_of
from services.docgen import build, pdf
from services.docgen.refresh import PostingClosed, refresh_before_build

logger = logging.getLogger(__name__)

# What the file is called when it is downloaded.
FILENAME_SUFFIX = "CV"

# One retry, and only for length.
MAX_TAILOR_CALLS = 2


class NotBuildable(RuntimeError):
    """This account or this job cannot produce the document, and retrying will not change that."""


@dataclass(frozen=True)
class Built:
    filename: str
    pdf: bytes
    pages: int | None
    # Model calls: none for an instant document, two when a CV came back too long.
    calls: int
    # Counted because every revision is a second model call and a second render: if this is
    # common, the fix belongs in the prompt rather than in the loop that cleans up after it.
    revised: bool = False


def run_instant(session, user_id: _uuid.UUID, job_id: int, *, kind: str) -> Built:
    """The free document: their own lines, picked and ordered by the posting. No model, no key."""
    user, job, _score = _load(session, user_id, job_id)
    documents = documents_of(session, user_id)
    if not (documents.get("cv") or documents.get("linkedin")):
        raise NotBuildable("upload your CV first — the instant version is built from it")
    html = instant.render(kind, documents=documents, job=job,
                          identity={"full_name": user.full_name, "links": user.links or []})
    # Their own text, so invisible characters a PDF export left in it are dropped, not refused.
    document, report = _render(build.strip_hidden(html), kind=kind)
    return _done(session, user, job, kind=kind, mode="instant", document=document,
                 report=report, calls=0)


def run_ai(session, user_id: _uuid.UUID, job_id: int, *, client, model: str, kind: str,
           http: object | None = None) -> Built:
    """The tailored document, written by the model on the key that pays for it."""
    user, job, score = _load(session, user_id, job_id)
    if not (job.description or "").strip():
        # Without the posting there is nothing to tailor to, and the result is the generic CV.
        raise NotBuildable("this posting has no description stored — nothing to tailor against")

    # Before anything is spent.
    if http is not None:
        try:
            refresh_before_build(session, job, http)
        except PostingClosed as exc:
            raise NotBuildable(str(exc)) from exc

    # The reference instance's `CLAUDE.md` and skill files, per user (`libs/profiles`).
    files = ProfileFiles(session, user_id)
    try:
        grounding = files.grounding()
    except ProfileIncomplete as exc:
        raise NotBuildable(str(exc)) from exc

    # Their own CV, written by the setup agent into `base_cv.html`, not rendered from a schema.
    base_cv = files.read("base_cv.html")
    if not base_cv or not base_cv.strip():
        raise NotBuildable("this account has no base CV yet — finish setup first")

    context = build.job_context(job, score=score,
                                fit_notes=_fit_notes(session, user_id, job_id),
                                salary_bands=_salary_bands(user))
    if kind == "cover":
        document, report, calls = _cover(
            client, context, model=model, base_cv=base_cv, grounding=grounding,
            template=files.read("tailor_cover.md"))
    else:
        document, report, calls = _tailor_until_it_fits(
            client, context, model=model, base_cv=base_cv, facts_md=grounding,
            template=files.read("tailor_cv.md"), role_angles=files.read("08-role-angles.md"))
    return _done(session, user, job, kind=kind, mode="ai", document=document, report=report,
                 calls=calls)


def _done(session, user: User, job: CorpusJob, *, kind: str, mode: str, document: bytes,
          report: pdf.PdfReport, calls: int) -> Built:
    """Record the build and say so, once."""
    _record(session, user.id, job)
    if not report.ats_ok:
        # A warning rather than a refusal: the layout is single-column and known good.
        logger.warning("docgen: ATS check flagged %s for user %s: %s",
                       job.url, user.id, report.issues)
    audit(logger, "docgen.built", user=user.id, job=job.id, mode=mode, kind=kind,
          pages=report.pages, calls=calls, revised=calls > 1, ats_ok=report.ats_ok,
          chars=report.chars)
    return Built(_filename(user.full_name, kind), document, report.pages, calls,
                 revised=calls > 1)


def _cover(client, context: str, *, model: str, base_cv: str, grounding: str | None,
           template: str | None = None) -> tuple[bytes, pdf.PdfReport, int]:
    """One call, no revision loop."""
    letter = build.tailor_cover(client, base_cv, context, model=model, facts_md=grounding,
                                template=template)
    return (*_render(letter, kind="cover"), 1)


def _tailor_until_it_fits(client, context: str, *, model: str, base_cv: str,
                          facts_md: str | None, template: str | None = None,
                          role_angles: str | None = None) -> tuple[bytes, pdf.PdfReport, int]:
    """Tailor, render once, and if the PDF is too long, say so once and tailor again.

    The document that gets delivered is the one that was measured — there is no separate probe
    render. A CV that fits is one render; only a CV that came back too long is two.
    """
    html = build.tailor(client, base_cv, context, model=model, facts_md=facts_md,
                        template=template, role_angles=role_angles)
    document, report = _render(html, kind="cv")

    feedback = build.page_feedback(report.pages)
    if feedback is None:
        return document, report, 1

    logger.info("docgen: %s pages, asking for one revision", report.pages)
    retried = build.tailor(client, base_cv, context, model=model, facts_md=facts_md,
                           template=template, role_angles=role_angles, feedback=feedback)
    second, second_report = _render(retried, kind="cv")

    # Keep whichever is shorter, and deliver the document that was actually measured.
    if second_report.pages is not None and (report.pages is None
                                            or second_report.pages < report.pages):
        return second, second_report, 2
    return document, report, 2


def _render(html: str, *, kind: str) -> tuple[bytes, pdf.PdfReport]:
    """Check the document, then render it once.

    Everything that can be answered from the markup is answered before Chrome is asked: a
    hidden-text injection costs a string scan rather than a render, and a document that fails it
    never reaches the renderer at all.
    """
    leftover = build.scan_hidden(html)
    if leftover:
        # Fail closed.
        raise NotBuildable(f"the generated document contained hidden content: "
                           f"{'; '.join(leftover)}")

    # The prompt is checked for markers before the call (`build._require_personalised`); this checks
    # what came back. The document goes to an employer, so it fails closed rather than shipping a CV
    # addressed to `<<NAME>>`.
    markers = [marker for marker in PLACEHOLDER_MARKERS if marker in html]
    if markers:
        raise NotBuildable("the generated document still carried a placeholder: "
                           f"{', '.join(markers)}")

    document = pdf.render(html)
    return document, pdf.inspect_pdf(document, kind=kind)


def _fit_notes(session, user_id: _uuid.UUID, job_id: int) -> str | None:
    """What the labeller concluded about this job, in its own fifteen words."""
    return session.scalar(
        select(UserLabel.rationale)
        .where(UserLabel.user_id == user_id, UserLabel.job_id == job_id))


def _load(session, user_id: _uuid.UUID, job_id: int) -> tuple[User, CorpusJob, float | None]:
    """Everything the build needs, with the reasons it cannot run stated as refusals."""
    user = session.get(User, user_id)
    if user is None:
        raise NotBuildable(f"no such user: {user_id}")

    job = session.get(CorpusJob, job_id)
    if job is None:
        raise NotBuildable(f"job {job_id} is no longer in the corpus")

    state = session.get(UserJobState, (user_id, job_id))
    return user, job, (state.score if state else None)


def _salary_bands(user: User) -> dict | None:
    """This user's target ask bands, from their config."""
    config = getattr(user, "config", None)
    return (getattr(config, "config", None) or {}).get("salary_bands") if config else None


def _record(session, user_id: _uuid.UUID, job: CorpusJob) -> None:
    """The durable half of an ephemeral document."""
    state = session.get(UserJobState, (user_id, job.id))
    if state is None:
        state = UserJobState(user_id=user_id, job_id=job.id)
        session.add(state)
    if state.applied_at is None:
        state.applied_at = func.now()

    # No `pin()` any more: `applied_at` above IS the record that keeps this posting out of the
    # archive, asked directly by `libs.corpus.archive` rather than copied to a flag.
    session.flush()


def _filename(full_name: str | None, kind: str = "cv") -> str:
    """`Alice_Example_CV.pdf`, from the name the user typed at signup."""
    label = "Cover" if kind == "cover" else FILENAME_SUFFIX
    slug = re.sub(r"[^A-Za-z0-9]+", "_", full_name or "").strip("_")
    return f"{slug}_{label}.pdf" if slug else f"{label}.pdf"
