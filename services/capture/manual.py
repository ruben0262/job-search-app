"""Manual entry — the path onboarding takes when capture cannot."""
from __future__ import annotations

from libs.contracts.profile import ProfileSchema
from services.capture.process import CaptureFailed


def manual_profile(fields: dict) -> ProfileSchema:
    """Validate a hand-filled profile through exactly the same schema as a captured one."""
    return ProfileSchema.model_validate(fields)


def capture_or_manual(run, fallback: dict) -> tuple[ProfileSchema, str]:
    """Try processing; fall back to the supplied form data."""
    try:
        return run(), "capture"
    except CaptureFailed:
        return manual_profile(fallback), "manual"
