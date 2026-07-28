"""Onboarding: what a new account must supply before it can produce a feed.

Onboarding **collects and nothing more** (`ONBOARDING_AND_PROCESSING.md` §1). No LLM runs here — not
one call — which is why it costs the user nothing, finishes in one sitting, and works while
Anthropic is down. The expensive, model-driven work (*process data*, *train*) happens later, on the
dashboard, where a user can see what it is about to spend.
"""
from libs.onboarding.state import CLAUDE_KINDS, OnboardingState, PageState, onboarding_state

__all__ = ["CLAUDE_KINDS", "OnboardingState", "PageState", "onboarding_state"]
