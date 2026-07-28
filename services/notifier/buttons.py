"""The buttons attached to a delivered job.

**Here rather than in the API**, even though the API is what receives the taps: `services.notifier`
may not import `services.api`, and the keyboard is a rendering concern belonging to whatever sends
the message. The two sides agree on the `callback_data` grammar and nothing else — which is the
narrowest possible coupling, and it is asserted by a test that reads both.
"""
from __future__ import annotations

# `ap:<job_id>`, `sk:<job_id>`, `bc:<job_id>`. Telegram caps `callback_data` at 64 bytes, so it
# carries an action and an id and nothing else — everything about *who* comes from the webhook URL.
ACTION_CODES = {"applied": "ap", "skipped": "sk", "build_cv": "bc"}


def keyboard(job_id: int) -> dict:
    """Three buttons. Every extra one is a thing to read on a phone before deciding, and the whole
    value of a feed is that it can be triaged quickly."""
    return {
        "inline_keyboard": [[
            {"text": "✅ Applied", "callback_data": f"ap:{job_id}"},
            {"text": "🚫 Skip", "callback_data": f"sk:{job_id}"},
            {"text": "📄 Build CV", "callback_data": f"bc:{job_id}"},
        ]],
    }
