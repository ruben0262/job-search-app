"""The buttons attached to a delivered job. **Two, exactly as the reference feed has.**

**Here rather than in the API**, even though the API is what receives the taps: `services.notifier`
may not import `services.api`, and the keyboard is a rendering concern belonging to whatever sends
the message. The two sides agree on the `callback_data` grammar and nothing else — which is the
narrowest possible coupling, and it is asserted by a test that reads both.

## Why there is no Applied or Skip button (decided 2026-07-29)

**A build is the positive.** Building a CV or a cover letter spends the user's own credit, so nobody
presses it on a job they are not going to apply to. That makes it a stronger signal than a tap on
"Applied" — backed by spending rather than by intention — and it asks nothing further of somebody
who has already decided.

**A skip is not a negative.** A card scrolled past may have been rejected, or it may have been
missed, read on a phone in a queue, or left for later. Training on it teaches the model that
inattention is dislike. The reference instance learned from positives for this reason and this feed
does the same: `libs/feedback` still records a `skipped` label if anything ever produces one, but
nothing on a card asks for it.
"""
from __future__ import annotations

# `bc:<job_id>` and `co:<job_id>`. Telegram caps `callback_data` at 64 bytes, so it carries an
# action and an id and nothing else — everything about *who* comes from the webhook URL.
#
# `ap` and `sk` stay in the grammar because the API still understands them: cards delivered before
# this change are still sitting in a channel with those buttons on them, and a tap on one months
# later must not become an unknown action.
ACTION_CODES = {"applied": "ap", "skipped": "sk", "build_cv": "bc", "build_cover": "co"}


def keyboard(job_id: int) -> dict:
    """The reference feed's two buttons: build a CV, or build a cover letter."""
    return {
        "inline_keyboard": [[
            {"text": "📄 Build CV", "callback_data": f"bc:{job_id}"},
            {"text": "✉️ Build Cover", "callback_data": f"co:{job_id}"},
        ]],
    }
