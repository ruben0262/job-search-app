"""The wire codes and keyboards for a Telegram card.

**One definition, because two disagreed.** `callback_data` is capped at 64 bytes, so a tap carries
an action code and a job id and nothing else — which means the service that *emits* a button and the
service that *parses* the tap have to agree on four two-letter strings, and `services` may not import
`services`. There were three copies of that agreement:

- `services/notifier/buttons.py` — the keyboard actually sent, with the two Build buttons, plus an
  `ACTION_CODES` map whose only consumer was a test asserting it matched the parser's copy.
- `services/api/telegram_routes.py` — a second `keyboard()` still returning the pre-2026-07-29
  buttons (✅ Applied / 🚫 Skip / 📄 Build CV), sent by nothing, exported in `__all__`, and asserted
  by `test_the_keyboard_carries_three_buttons_within_telegrams_limit`. A test that passed against the
  dead path while the feed sent something else.
- the parse table, `CALLBACK_PREFIX`, written out as a fourth literal.

The codes are now defined once and the keyboards are built *from* them, so a button cannot carry a
prefix the parser does not know: the drift is not tested for, it is unrepresentable.
"""
from __future__ import annotations

# What each action is called on the wire.
CALLBACK_CODES: dict[str, str] = {
    "applied": "ap",
    "skipped": "sk",
    "build_cv": "bc",
    "build_cover": "co",
}

# The parse direction. One dict inverted, never a second literal.
ACTION_FOR_CODE: dict[str, str] = {code: action for action, code in CALLBACK_CODES.items()}


def _button(text: str, action: str, job_id: int) -> dict:
    """One button, whose `callback_data` is built from the code table the parser reads."""
    return {"text": text, "callback_data": f"{CALLBACK_CODES[action]}:{job_id}"}


def feed_keyboard(job_id: int) -> dict:
    """What a delivered job carries: build a CV, or build a cover letter."""
    return {
        "inline_keyboard": [[
            _button("📄 Build CV", "build_cv", job_id),
            _button("✉️ Build Cover", "build_cover", job_id),
        ]],
    }


def rating_keyboard(job_id: int) -> dict:
    """Two answers and nothing else. `/rate` asks one question: would you have applied?"""
    return {
        "inline_keyboard": [[
            _button("👍 Would apply", "applied", job_id),
            _button("👎 Not for me", "skipped", job_id),
        ]],
    }
