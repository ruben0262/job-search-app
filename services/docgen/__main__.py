"""The `docgen` lane worker: one CV or cover letter per job, kept for download, then deleted."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager

import httpx

import libs.notify as notifylib
from libs import settings
from libs.db import database, ensure_schema
from libs.db.models import QueueJob
from libs.db.models.tenant import record_state
from libs.documents.saved import keep, purge_unsaved
from libs.legal import has_consented
from libs.llm import LlmError, paying_client
from libs.obs import configure
from libs.proxy import site_proxy
from libs.queue.worker import INTERACTIVE_IDLE_SLEEP_SEC, Terminal, run_worker
from libs.secrets import SecretStore, install, redacting
from services.docgen import pdf, run
from services.docgen.run import NotBuildable

logger = logging.getLogger(__name__)


def handle(db, job: QueueJob) -> None:
    """Build the document this job asks for, and keep it for the person to download."""
    payload = job.payload or {}
    job_id = payload.get("job_id")
    if not isinstance(job_id, int):
        raise Terminal(f"docgen job {job.id} carries no corpus job id")
    kind, mode = payload.get("kind") or "cv", payload.get("mode")

    try:
        if kind not in ("cv", "cover"):
            raise NotBuildable(f"unknown document kind {kind!r}")
        if mode == "instant":
            built = run.run_instant(db, job.user_id, job_id, kind=kind)
        elif mode == "ai":
            built = _with_ai(db, job, job_id=job_id, kind=kind, payer=payload.get("payer"))
        else:
            # A Telegram tap from before documents moved onto the site: no way to hand it over.
            raise NotBuildable("documents are built on the site now — open the job there")
    except NotBuildable as exc:
        # Not a failure, and it must not be recorded as one: the reason is the page's answer.
        db[QueueJob.COLLECTION].update_one({"_id": job.id},
                                           {"$set": {"payload": {**payload, "refused": str(exc)}}})
        notifylib.notify(db, job.user_id, notifylib.DOCUMENT_NOT_READY, str(exc))
        logger.info("docgen: %s cannot build yet — %s", job.user_id, exc)
        return
    keep(db, build_id=job.id, user_id=job.user_id, job_id=job_id, kind=kind, mode=mode,
         filename=built.filename, pdf=built.pdf, ats_ok=built.ats_ok)
    if kind == "cover":
        # What retention keeps a posting for (`libs.corpus.archive.ACTED_ON`). Nothing set it before
        # 2026-11-03, so a posting somebody wrote a cover letter for aged out like any other.
        record_state(db, job.user_id, job_id, cover_built=True)


def _with_ai(db, job: QueueJob, *, job_id: int, kind: str, payer: str | None):
    """The model's version, and only with consent: this is where their text leaves the server."""
    if not has_consented(db, job.user_id):
        raise NotBuildable("agree to how AI-written documents use your profile first — it is on "
                           "the privacy page")
    store = SecretStore(db)
    with paying_client(store, job.user_id, payer or "") as paid, \
            _http_for() as http:
        if paid is None:
            raise NotBuildable("the key that was to pay for this has been removed — add one, or "
                               "use the instant version")
        client, provider = paid
        try:
            return run.run_ai(db, job.user_id, job_id, client=client, kind=kind, http=http,
                              model=settings.document_model(db, provider))
        except LlmError as exc:
            # Their key, and the operator should see a run of these.
            notifylib.notify(db, job.user_id, notifylib.DOCUMENT_NOT_READY,
                             "The AI key could not write this document.")
            raise Terminal(str(exc)) from exc


@contextmanager
def _http_for():
    """An HTTP client for the pre-build freshness check: through the site proxy when there is one, as
    the boards that refuse a server's address are what it most often checks."""
    proxy = site_proxy()
    with redacting(proxy or ""), httpx.Client(timeout=25, follow_redirects=True, proxy=proxy) as client:
        yield client


def purge(db) -> None:
    """D9 for a document that was not kept: deleted once it is `document_keep_hours` old."""
    hours = settings.document_keep_hours(db)
    gone = purge_unsaved(db, hours)
    if gone:
        logger.info("docgen: deleted %d unkept document(s) older than %d hour(s)", gone, hours)


def main() -> int:
    configure("docgen")
    install()
    ensure_schema()
    # Launched now, so the first person after a restart does not wait for it.
    pdf.browser()
    run_worker(database(), "docgen", os.environ.get("WORKER_NAME", "docgen-1"), handle,
               idle_sleep=INTERACTIVE_IDLE_SLEEP_SEC, periodic=purge)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
