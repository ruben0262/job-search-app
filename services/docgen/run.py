"""One `docgen` queue job, end to end: tailor, compile, check, deliver, record, discard."""
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
from libs.db.models import CorpusJob, User, UserJobState, UserLabel
from libs.obs import audit
from libs.profiles import ProfileIncomplete
from libs.profiles.repository import ProfileFiles
from services.docgen import build, pdf
from services.docgen.refresh import PostingClosed, refresh_before_build

logger = logging.getLogger(__name__)

# What the file is called when it lands in the user's chat.
FILENAME_SUFFIX = "CV"

# One retry, and only for length.
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
    # Whether the first attempt came back too long and had to be written again. Counted because
    # every revision is a second frontier call and a second Chrome launch: if this is common, the
    # fix belongs in the prompt rather than in the loop that cleans up after it.
    revised: bool = False


def run_build(session, user_id: _uuid.UUID, job_id: int, *,
              client,
              send_document: Callable[..., None],
              token: str,
              kind: str = "cv",
              http: object | None = None,
              workdir: pathlib.Path | None = None) -> BuildResult:
    """Build one tailored CV for one posting and deliver it."""
    if kind not in ("cv", "cover"):
        raise NotBuildable(f"unknown document kind {kind!r}")
    user, job, score = _load(session, user_id, job_id)

    # Before anything is spent.
    if http is not None:
        try:
            refresh_before_build(session, job, http)
        except PostingClosed as exc:
            raise NotBuildable(str(exc)) from exc
    profile = ProfileSchema.model_validate(user.profile)

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

    scratch = pathlib.Path(workdir) if workdir else pathlib.Path(tempfile.mkdtemp(prefix="docgen-"))
    try:
        context = build.job_context(job, score=score,
                                    fit_notes=_fit_notes(session, user_id, job_id),
                                    salary_bands=_salary_bands(user))
        filename = _filename(profile, kind)

        if kind == "cover":
            document, report, calls = _cover(
                client, profile, context, base_cv=base_cv, grounding=grounding, scratch=scratch,
                filename=filename, template=files.read("tailor_cover.md"))
        else:
            document, report, calls = _tailor_until_it_fits(
                client, profile, context, base_cv=base_cv, facts_md=grounding, scratch=scratch,
                filename=filename, template=files.read("tailor_cv.md"),
                role_angles=files.read("08-role-angles.md"))
        pages = report.pages

        # The personal chat, never a feed channel.
        chat_id = user.telegram_chat_id
        if not chat_id:
            raise NotBuildable("this account has no personal Telegram chat to deliver to")

        send_document(token, chat_id, document.read_bytes(), document.name,
                      _caption(job))
        _record(session, user_id, job)

        if not report.ats_ok:
            # A warning rather than a refusal: the layout is single-column and known good.
            logger.warning("docgen: ATS check flagged %s for user %s: %s",
                           job.url, user_id, report.issues)

        # `revised` is the number to watch: it is how often the prompt produced a document that did
        # not fit, and each one costs a second model call and a second render.
        audit(logger, "docgen.built", user=user_id, job=job_id, pages=report.pages,
              calls=calls, revised=calls > 1, ats_ok=report.ats_ok, chars=report.chars)
        return BuildResult(job_id=job_id, pages=pages, calls=calls, ats_ok=report.ats_ok,
                           delivered_to=chat_id, revised=calls > 1)
    finally:
        # D9.
        shutil.rmtree(scratch, ignore_errors=True)


def _cover(client, profile: ProfileSchema, context: str, *,
           base_cv: str, grounding: str | None, scratch: pathlib.Path, filename: str,
           template: str | None = None) -> tuple[pathlib.Path, pdf.PdfReport, int]:
    """One call, no revision loop."""
    letter = build.tailor_cover(client, base_cv, context, facts_md=grounding,
                                template=template)
    return (*_render(letter, scratch, filename, kind="cover"), 1)


def _tailor_until_it_fits(client, profile: ProfileSchema, context: str, *,
                          base_cv: str, facts_md: str | None, scratch: pathlib.Path,
                          filename: str, template: str | None = None,
                          role_angles: str | None = None,
                          ) -> tuple[pathlib.Path, pdf.PdfReport, int]:
    """Tailor, render once, and if the PDF is too long, say so once and tailor again.

    The document that gets delivered is the one that was measured — there is no separate probe
    render. A CV that fits is one Chrome launch; only a CV that came back too long is two.
    """
    html = build.tailor(client, base_cv, context, facts_md=facts_md, template=template,
                        role_angles=role_angles)
    document, report = _render(html, scratch, filename, kind="cv")

    feedback = build.page_feedback(report.pages)
    if feedback is None:
        return document, report, 1

    logger.info("docgen: %s pages, asking for one revision", report.pages)
    retried = build.tailor(client, base_cv, context, facts_md=facts_md,
                           template=template, role_angles=role_angles, feedback=feedback)
    second, second_report = _render(retried, scratch, f"retry-{filename}", kind="cv")

    # Keep whichever is shorter, and deliver the file that was actually measured. The winner takes
    # the real filename — Telegram shows it to the user, and "retry-Alice_CV.pdf" is not a CV.
    if second_report.pages is not None and (report.pages is None
                                            or second_report.pages < report.pages):
        return second.replace(scratch / filename), second_report, 2
    return document, report, 2


def _render(html: str, scratch: pathlib.Path, filename: str,
            *, kind: str) -> tuple[pathlib.Path, pdf.PdfReport]:
    """Check the document, then render it once.

    Everything that can be answered from the markup is answered before Chrome starts: a hidden-text
    injection costs a string scan rather than a browser launch, and a document that fails it never
    reaches the renderer at all.
    """
    leftover = build.scan_hidden(html)
    if leftover:
        # Fail closed.
        raise NotBuildable(f"the generated document contained hidden content: "
                           f"{'; '.join(leftover)}")

    source = scratch / f"{pathlib.Path(filename).stem}.html"
    source.write_text(html, encoding="utf-8")
    document = pdf.compile_pdf(source, scratch / filename)
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
    if not user.profile:
        raise NotBuildable("this account has no confirmed profile — finish onboarding first")

    job = session.get(CorpusJob, job_id)
    if job is None:
        raise NotBuildable(f"job {job_id} is no longer in the corpus")
    if not (job.description or "").strip():
        # Without the posting there is nothing to tailor to, and the result is the generic CV.
        raise NotBuildable("this posting has no description stored — nothing to tailor against")

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


def _filename(profile: ProfileSchema, kind: str = "cv") -> str:
    """`Alice_Example_CV.pdf`."""
    label = "Cover" if kind == "cover" else FILENAME_SUFFIX
    slug = re.sub(r"[^A-Za-z0-9]+", "_", profile.identity.name).strip("_")
    return f"{slug}_{label}.pdf" if slug else f"{label}.pdf"


def _caption(job: CorpusJob) -> str:
    company = f" · {job.company}" if job.company else ""
    return f"{job.title}{company}\n{job.url}"
