"""One `docgen` build, end to end: write the document, check it, render it once, record it.

**AI** writes it: the tailoring prompts, unchanged, on whichever key `libs.llm.resolve` chose, for an
account with the personalised setup files those prompts need. The PDF comes back as bytes for the
worker to keep until it is downloaded (D9). The instant document, written by code from the person's
uploads, was removed on 2026-11-08 (owner: every document is written by AI, and where AI cannot be
used the person is told so).
"""
from __future__ import annotations

import logging
import re
import uuid as _uuid
from dataclasses import dataclass

from libs import templates
from libs.db import get
from libs.db.models import CorpusJob, User
from libs.obs import audit
from libs.profiles import PLACEHOLDER_MARKERS, ProfileIncomplete
from libs.profiles.repository import ProfileFiles
from libs.tenant.repository import TenantRepository
from services.docgen import build, pdf
from services.docgen.refresh import PostingClosed, refresh_before_build

logger = logging.getLogger(__name__)

# What the file is called when it is downloaded.
FILENAME_SUFFIX = "CV"


class NotBuildable(RuntimeError):
    """This account or this job cannot produce the document, and retrying will not change that."""


@dataclass(frozen=True)
class Built:
    filename: str
    pdf: bytes
    pages: int | None
    # Model calls: one, or two when a CV came back too long.
    calls: int
    # Counted because every revision is a second model call and a second render: if this is
    # common, the fix belongs in the prompt rather than in the loop that cleans up after it.
    revised: bool = False
    # Whether the PDF passed the ATS check; kept with the document (`BuiltDocument.ats_ok`).
    ats_ok: bool | None = None


def run_ai(db, user_id: _uuid.UUID, job_id: int, *, client, model: str, kind: str,
           http: object | None = None) -> Built:
    """The tailored document, written by the model on the key that pays for it."""
    user, job = _load(db, user_id, job_id)
    if not (job.description or "").strip():
        # Without the posting there is nothing to tailor to, and the result is the generic CV.
        raise NotBuildable("this posting has no description stored — nothing to tailor against")

    # Before anything is spent.
    if http is not None:
        try:
            refresh_before_build(db, job, http)
        except PostingClosed as exc:
            raise NotBuildable(str(exc)) from exc

    # The reference instance's `CLAUDE.md` and skill files, per user (`libs/profiles`).
    files = ProfileFiles(db, user_id)
    try:
        grounding = files.grounding()
    except ProfileIncomplete as exc:
        raise NotBuildable(str(exc)) from exc

    # Their own CV, written by the setup agent into `base_cv.html`, not rendered from a schema.
    base_cv = files.read("base_cv.html")
    if not base_cv or not base_cv.strip():
        raise NotBuildable("this account has no base CV yet — finish setup first")

    # No score and no fit notes since the model was switched off: what the build was given then too.
    context = build.job_context(job, score=None, fit_notes=None,
                                salary_bands=_salary_bands(db, user_id))
    # The look they picked: the model writes the words, the template lays them out.
    look = templates.chosen(user, kind)
    if kind == "cover":
        document, report, calls = _cover(
            client, context, model=model, look=look, grounding=grounding,
            template=files.read("tailor_cover.md"))
    else:
        document, report, calls = _tailor_until_it_fits(
            client, context, model=model, base_cv=base_cv, style=look.style, facts_md=grounding,
            template=files.read("tailor_cv.md"), role_angles=files.read("08-role-angles.md"))
    return _done(db, user, job, kind=kind, mode="ai", document=document, report=report,
                 calls=calls)


def _done(db, user: User, job: CorpusJob, *, kind: str, mode: str, document: bytes,
          report: pdf.PdfReport, calls: int) -> Built:
    """Say the build happened, once. It is not an application: "Mark as applied" on the jobs page is."""
    if not report.ats_ok:
        # A warning rather than a refusal: the layout is single-column and known good.
        logger.warning("docgen: ATS check flagged %s for user %s: %s",
                       job.url, user.id, report.issues)
    audit(logger, "docgen.built", user=user.id, job=job.id, mode=mode, kind=kind,
          pages=report.pages, calls=calls, revised=calls > 1, ats_ok=report.ats_ok,
          chars=report.chars)
    return Built(_filename(user.full_name, kind), document, report.pages, calls,
                 revised=calls > 1, ats_ok=report.ats_ok)


def _cover(client, context: str, *, model: str, look: templates.Template, grounding: str | None,
           template: str | None = None) -> tuple[bytes, pdf.PdfReport, int]:
    """One call, no revision loop."""
    letter = build.tailor_cover(client, context, model=model, head=look.head, style=look.style,
                                facts_md=grounding, template=template)
    return (*_render(letter, kind="cover"), 1)


def _tailor_until_it_fits(client, context: str, *, model: str, base_cv: str, style: str,
                          facts_md: str | None, template: str | None = None,
                          role_angles: str | None = None) -> tuple[bytes, pdf.PdfReport, int]:
    """Tailor, render once, and if the PDF is too long, say so once and tailor again.

    The document that gets delivered is the one that was measured — there is no separate probe
    render. A CV that fits is one render; only a CV that came back too long is two.
    """
    html = build.tailor(client, base_cv, context, model=model, style=style, facts_md=facts_md,
                        template=template, role_angles=role_angles)
    document, report = _render(html, kind="cv")

    feedback = build.page_feedback(report.pages)
    if feedback is None:
        return document, report, 1

    logger.info("docgen: %s pages, asking for one revision", report.pages)
    retried = build.tailor(client, base_cv, context, model=model, style=style, facts_md=facts_md,
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


def _load(db, user_id: _uuid.UUID, job_id: int) -> tuple[User, CorpusJob]:
    """Everything the build needs, with the reasons it cannot run stated as refusals."""
    user = get(db, User, user_id)
    if user is None:
        raise NotBuildable(f"no such user: {user_id}")

    job = get(db, CorpusJob, job_id)
    if job is None:
        raise NotBuildable(f"job {job_id} is no longer in the corpus")
    return user, job


def _salary_bands(db, user_id: _uuid.UUID) -> dict | None:
    """This user's target ask bands, from their config."""
    return (TenantRepository(db).get_config(user_id) or {}).get("salary_bands")


def _filename(full_name: str | None, kind: str = "cv") -> str:
    """`Alice_Example_CV.pdf`, from the name the user typed at signup."""
    label = "Cover" if kind == "cover" else FILENAME_SUFFIX
    slug = re.sub(r"[^A-Za-z0-9]+", "_", full_name or "").strip("_")
    return f"{slug}_{label}.pdf" if slug else f"{label}.pdf"
