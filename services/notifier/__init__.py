"""Per-user Telegram delivery — one bot token per user, per-user intervals, delivery dedup.

Telegram stays the delivery channel; the web app handles signup, config, and monitoring
(decided 2026-07-26). Each user connects their own bot, so a revoked token or a blocked bot is one
user's problem rather than the platform's.

**Platform alerts never travel this path.** One of the failure modes *is* Telegram, and a channel
cannot report its own death — health problems reach users by email and an in-app banner
(OPERATIONS_PLAN.md §4b). Resist the temptation to reuse the bot for status messages.

Delivery dedup is the part worth care: a restarted worker must not re-send a digest. The claim is
transactional and ``next_run_at`` is re-armed on restart (SCHEDULER_PLAN.md §4), so a crash costs
a retry, never a duplicate in someone's chat.

Restart blast radius: deliveries queue up.
"""
