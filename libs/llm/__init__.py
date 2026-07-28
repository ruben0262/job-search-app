"""The one seam between this platform and Anthropic.

In `libs` rather than in a service because **two services call Claude on the user's behalf** —
`capture` builds a profile, `labeller` scores jobs against a rubric — and services may not import
each other (`CODE_STRUCTURE.md` §2). A copy in each is how the OAuth header ends up correct in one
and missing in the other.

Every call here spends **the user's own credential**, never a platform key. That is what keeps the
free plan free, keeps abuse uninteresting, and makes cost something the user can see rather than a
line item they cannot.
"""
from libs.llm.client import (
    CLAUDE_KINDS,
    CLAUDE_OAUTH_BETA,
    AnthropicClient,
    LlmClient,
    LlmError,
    client_for,
)

__all__ = ["CLAUDE_KINDS", "CLAUDE_OAUTH_BETA", "AnthropicClient", "LlmClient", "LlmError",
           "client_for"]
