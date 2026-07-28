"""What a user does with a job, as training data.

Everything upstream ranks jobs by what a model thought of them against a rubric. This records what
the person actually **did** — applied, skipped — which is the higher-quality signal and the only one
that reflects a decision rather than an opinion (``ONBOARDING_AND_PROCESSING.md`` §5).

In ``libs`` because the Telegram webhook and the web dashboard must record it identically. Two
implementations would drift, and the drift would be silent: two ways of recording "applied" that
disagree about the label, and a training set that quietly means two things.
"""
from libs.feedback.actions import (
    ACTIONS,
    APPLIED_LABEL,
    FEEDBACK_WEIGHT,
    SKIPPED_LABEL,
    FeedbackResult,
    record,
)

__all__ = ["ACTIONS", "APPLIED_LABEL", "FEEDBACK_WEIGHT", "SKIPPED_LABEL", "FeedbackResult",
           "record"]
