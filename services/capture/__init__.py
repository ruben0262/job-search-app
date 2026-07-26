"""Profile capture — the sandboxed Managed Agents interview that turns a signup into a tenant.

A user's rubric, facts block, and base CV cannot be typed into a form without losing most of what
makes scoring work. Capture runs as an **Anthropic-hosted Managed Agents session**: restricted
toolset, read-only document mounts, deny-by-default egress, approval-gated writes
(PROFILE_CAPTURE_PLAN.md). It ends in a confirmation step the user actually reads.

**The interview is agentic; the rendering is not.** Everything downstream of confirmation —
rubric, facts block, feature token lists — is deterministic code in ``libs.scoring``. Keeping that
line sharp is what makes a profile reproducible and a regression bisectable.

Three things carried knowingly:

1. **Managed Agents is beta and sits on the onboarding critical path.** Pin the beta header
   explicitly rather than tracking latest; expect one contract change during the build.
2. **Manual entry is always the fallback** and ships in the same commit — capture must never
   hard-block onboarding.
3. **Capture cost is unmeasured** and it is the first money a user spends, on a free plan, on
   their own key. Measure before Phase 4; if it is high, the free plan needs an attempt cap
   (PLATFORM_ARCHITECTURE.md §10.10).

It spends the user's own credential, which is why an API key is required *before* capture in the
onboarding order.
"""
