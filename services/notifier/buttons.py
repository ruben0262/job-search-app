"""The buttons attached to a delivered job."""
from __future__ import annotations

# `bc:<job_id>` and `co:<job_id>`.
ACTION_CODES = {"applied": "ap", "skipped": "sk", "build_cv": "bc", "build_cover": "co"}


def keyboard(job_id: int) -> dict:
    """The reference feed's two buttons: build a CV, or build a cover letter."""
    return {
        "inline_keyboard": [[
            {"text": "📄 Build CV", "callback_data": f"bc:{job_id}"},
            {"text": "✉️ Build Cover", "callback_data": f"co:{job_id}"},
        ]],
    }
