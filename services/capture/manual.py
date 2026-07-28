"""Manual entry — the path onboarding takes when capture cannot.

Kept after the sandbox went away, and the reason changed rather than disappeared. Processing is
no longer a beta on the critical path, but it is still one call to somebody else's service on the
user's own credit — an outage, an expired token, or a document set the model cannot make sense of
must not leave an account with no way forward. The same form the confirmation step renders, filled
from scratch.

What is lost is real and worth stating: a hand-typed profile has no derived NOT-list, because
nobody self-reports absence accurately (`PROFILE_CAPTURE_PLAN.md` §1). The result is a weaker rubric
and a noisier feed — not a broken account. `capture_source` records which path a profile came from
so that difference is measurable rather than assumed.
"""
from __future__ import annotations

from libs.contracts.profile import ProfileSchema
from services.capture.client import CaptureFailed


def manual_profile(fields: dict) -> ProfileSchema:
    """Validate a hand-filled profile through exactly the same schema as a captured one.

    One schema, so a manual profile cannot be shaped differently from a captured one and quietly
    break the renderer six steps later.
    """
    return ProfileSchema.model_validate(fields)


def capture_or_manual(run, fallback: dict) -> tuple[ProfileSchema, str]:
    """Try processing; fall back to the supplied form data. Returns the profile and its source.

    `run` is a zero-argument call — usually a `functools.partial` of `process_user`. Written this
    way so the fallback decision lives in one place rather than being re-derived at every call site,
    which is how a fallback quietly stops being wired up.
    """
    try:
        return run(), "capture"
    except CaptureFailed:
        return manual_profile(fallback), "manual"
