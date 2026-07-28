"""The relabeller — the step that makes editing a profile survivable.

A profile change bumps ``profile_generation``, and every label written under the old rubric is not
merely old: it answers a different question. Without this worker a re-process leaves **zero** usable
labels at the new generation and training cannot run at all, so the account silently degrades to no
model rather than to a slightly stale one (``ONBOARDING_AND_PROCESSING.md`` §4).

It is the most expensive thing on the platform per user — one Haiku call per job at ~$0.0018
against thousands of jobs — which is why *process implies retrain* is enforced and *retrain implies
process* deliberately is not: forcing a re-process before a retrain would spend a user's money
re-deriving a profile from unchanged documents and invalidate the very labels the retrain was for.
"""
