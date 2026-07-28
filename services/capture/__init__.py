"""Process data — the step that turns a stack of documents into a profile.

It runs **on the dashboard, after onboarding**, not during it. Onboarding collects; this is the
first thing that spends the user's Claude credential, so it is a decision they make with the cost in
front of them rather than something that happens to them mid-signup
(`ONBOARDING_AND_PROCESSING.md` §1).

**The conversation is agentic; the rendering is not.** This package produces one JSON profile.
Everything downstream of confirmation — rubric, facts block, feature token lists — is deterministic
code in ``libs.scoring``. Keeping that line sharp is what makes a profile reproducible and a
regression bisectable.

Three properties worth stating, because each replaced an earlier decision that looked fine:

1. **Plain Messages, no sandbox.** Text is extracted server-side at upload, so nothing is uploaded
   to Anthropic and nothing is mounted. That removed a beta from the critical path and made OAuth
   viable — Managed Agents authenticates by API key only, which would have silently required every
   user to buy API credit and broken decision D2.
2. **The output is a draft.** ``users.profile_draft`` holds it until a human confirms. Applying a
   profile bumps ``profile_generation`` and invalidates every label the account has; nothing an
   uploaded CV can influence may do that unreviewed (`PROFILE_CAPTURE_PLAN.md` §5).
3. **Manual entry is always available.** One call to somebody else's service, on the user's own
   credit — an outage must not leave an account with no way forward.
"""
