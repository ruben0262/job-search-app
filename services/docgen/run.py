"""One `docgen` build, end to end: write the document, render it, check it, record it.

**AI** writes it, in one call (`build`), on the AI account the person linked, from what they uploaded
and switched on. The PDF comes back as bytes for the worker to keep until it is downloaded (D9). The
instant document, written by code from the person's uploads, was removed on 2026-11-08 (owner: every
document is written by AI, and where AI cannot be used the person is told so).
"""
from __future__ import annotations

import logging
import re
import time
import uuid as _uuid
from contextlib import contextmanager
from dataclasses import dataclass

from libs import prompts, templates
from libs.corpus.texts import with_texts
from libs.db import get
from libs.db.models import CorpusJob, User
from libs.obs import audit
from libs.suggestions import documents_of
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
    # Model calls: one, or two when the first answer failed a check.
    calls: int
    # Counted because every revision is a second model call and a second render: if this is
    # common, the fix belongs in the prompt rather than in the loop that cleans up after it.
    revised: bool = False
    # Whether the PDF passed the ATS check; kept with the document (`BuiltDocument.ats_ok`).
    ats_ok: bool | None = None
    # The wording that wrote it and the model it ran on, kept with the document for the outcomes.
    prompt_version: str | None = None
    model: str | None = None
    # In and out over its calls; None where the account's tool does not count them.
    tokens: int | None = None


def run_ai(db, user_id: _uuid.UUID, job_id: int, *, client, model: str, kind: str,
           http: object | None = None) -> Built:
    """The document for this job, written by the model on the account that pays for it."""
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

    request = build.prepare(kind, user=user, job=job, documents=documents_of(db, user_id),
                            look=templates.chosen(user, kind), languages=_languages(db, user_id))
    # Where the time goes and what each call used, said with the document (`_done`).
    seconds: dict[str, float] = {"write": 0.0, "render": 0.0}
    with _timed(seconds, "write"):
        reply, body = build.draft(client, request, model=model)
    used = [getattr(reply, "usage", None)]
    # The model that wrote the answer that is kept, where the provider names it; else the one asked for.
    wrote = getattr(reply, "model", None) or model
    with _timed(seconds, "render"):
        document, report = pdf.fitted(build.assemble(request, body), kind=kind,
                                      headings=build.headings(body))
    check = build.problems(request, body, report.pages, report.spilled)
    calls = 1
    if not check.ok:
        # Said back once, with only what was found. Kept only if it is no worse. The faults are not logged:
        # they quote the person's CV.
        logger.info("docgen: %s for %s: %d page(s) over, %d number(s) or name(s) not in their documents, "
                    "%d other fault(s); asking again", kind, user_id, check.pages_over, len(check.invented),
                    len(check.faults))
        calls = 2
        try:
            with _timed(seconds, "write"):
                again, second = build.draft(client, request, model=model,
                                            revision=(reply, build.feedback(request, check, report.pages)))
        except build.BuildFailed as exc:
            logger.warning("docgen: the second %s for %s came back unusable (%s); keeping the first", kind,
                           user_id, exc)
        else:
            used.append(getattr(again, "usage", None))
            with _timed(seconds, "render"):
                second_document, second_report = pdf.fitted(build.assemble(request, second), kind=kind,
                                                            headings=build.headings(second))
            second_check = build.problems(request, second, second_report.pages, second_report.spilled)
            if not second_check.worse_than(check):
                document, report, check = second_document, second_report, second_check
                wrote = getattr(again, "model", None) or model
    if not check.ok:
        # Delivered: a number the check could not find may be written another way in their CV ("1 200").
        logger.warning("docgen: %s for %s delivered with %d page(s) over, number(s) or name(s) not found in "
                       "their documents (%s) and %d other fault(s)", kind, user_id, check.pages_over,
                       ", ".join(check.invented) or "none", len(check.faults))
    return _done(db, user, job, kind=kind, mode="ai", document=document, report=report, calls=calls,
                 model=wrote, seconds=seconds, used=used)


@contextmanager
def _timed(seconds: dict[str, float], part: str):
    """Add how long the block took to `seconds[part]`."""
    started = time.monotonic()
    try:
        yield
    finally:
        seconds[part] += time.monotonic() - started


def _done(db, user: User, job: CorpusJob, *, kind: str, mode: str, document: bytes,
          report: pdf.PdfReport, calls: int, model: str, seconds: dict[str, float], used: list) -> Built:
    """Say the build happened, once, with what it took: the seconds the model wrote for and Chrome drew
    for, and the tokens in and out over its calls ("-" where the account's tool does not count them). It
    is not an application: "Mark as applied" on the jobs page is."""
    counts = [each for each in used if each is not None]
    tokens_in = sum(each.input_tokens for each in counts) if counts else "-"
    tokens_out = sum(each.output_tokens for each in counts) if counts else "-"
    if not report.ats_ok:
        # A warning rather than a refusal: the layout is single-column and known good.
        logger.warning("docgen: ATS check flagged %s for user %s: %s",
                       job.url, user.id, report.issues)
    version = prompts.version(build.PROMPTS[kind])
    audit(logger, "docgen.built", user=user.id, job=job.id, mode=mode, kind=kind,
          pages=report.pages, calls=calls, revised=calls > 1, ats_ok=report.ats_ok,
          chars=report.chars, scale=report.scale, prompt=version, model=model or "-",
          write_s=round(seconds["write"], 1), render_s=round(seconds["render"], 1),
          tokens_in=tokens_in, tokens_out=tokens_out)
    return Built(_filename(user.full_name, kind), document, report.pages, calls,
                 revised=calls > 1, ats_ok=report.ats_ok, prompt_version=version, model=model or None,
                 tokens=tokens_in + tokens_out if counts else None)


def _load(db, user_id: _uuid.UUID, job_id: int) -> tuple[User, CorpusJob]:
    """Everything the build needs, with the reasons it cannot run stated as refusals."""
    user = get(db, User, user_id)
    if user is None:
        raise NotBuildable(f"no such user: {user_id}")

    job = get(db, CorpusJob, job_id)
    if job is None:
        raise NotBuildable(f"job {job_id} is no longer in the corpus")
    return user, with_texts(db, [job])[0]


def _languages(db, user_id: _uuid.UUID) -> tuple[str, ...]:
    """The job languages their search accepts (My search): which languages they work in."""
    return tuple((TenantRepository(db).get_config(user_id) or {}).get("languages") or ())


def _filename(full_name: str | None, kind: str = "cv") -> str:
    """`Alice_Example_CV.pdf`, from the name the user typed at signup."""
    label = "Cover" if kind == "cover" else FILENAME_SUFFIX
    slug = re.sub(r"[^A-Za-z0-9]+", "_", full_name or "").strip("_")
    return f"{slug}_{label}.pdf" if slug else f"{label}.pdf"
