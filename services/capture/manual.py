"""Manual entry — the path onboarding takes when capture cannot.

Ships in the same commit as the adapter, deliberately. Managed Agents is beta and sits on the
onboarding critical path; a user whose capture fails must still be able to finish signing up. The
same form the confirmation step renders, filled from scratch.

What is lost is real and worth stating: a hand-typed profile has no derived NOT-list, because
nobody self-reports absence accurately (`PROFILE_CAPTURE_PLAN.md` §1). The result is a weaker rubric
and a noisier feed — not a broken account. `capture_source` records which path a profile came from
so that difference is measurable rather than assumed.
"""
from __future__ import annotations

from libs.contracts.profile import ProfileSchema
from services.capture.session import CaptureFailed, CaptureResult


def manual_profile(fields: dict) -> ProfileSchema:
    """Validate a hand-filled profile through exactly the same schema as a captured one.

    One schema, so a manual profile cannot be shaped differently from a captured one and quietly
    break the renderer six steps later.
    """
    return ProfileSchema.model_validate(fields)


def capture_or_manual(run, fallback: dict) -> tuple[ProfileSchema, str]:
    """Try capture; fall back to the supplied form data. Returns the profile and its source.

    `run` is a zero-argument call — usually a `functools.partial` of `run_capture`. Written this way
    so the fallback decision lives in one place rather than being re-derived at every call site,
    which is how a fallback quietly stops being wired up.
    """
    try:
        result: CaptureResult = run()
    except CaptureFailed:
        return manual_profile(fallback), "manual"
    return result.profile, "capture"
