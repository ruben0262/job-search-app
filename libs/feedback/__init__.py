"""What a user does with a job, as training data."""
from libs.feedback.actions import (
    ACTIONS,
    APPLIED_LABEL,
    FEEDBACK_WEIGHT,
    SKIPPED_LABEL,
    FeedbackResult,
    record,
)
from libs.feedback.rating import DEFAULT_THRESHOLD, next_to_rate, rating_pool

__all__ = ["ACTIONS", "APPLIED_LABEL", "DEFAULT_THRESHOLD", "FEEDBACK_WEIGHT", "SKIPPED_LABEL",
           "FeedbackResult", "next_to_rate", "rating_pool", "record"]
