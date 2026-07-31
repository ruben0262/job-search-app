"""The score at which a job counts as a match. One number, shared by everything that shows one.

**Three places had to agree and only two did.** The notifier delivers at 60; the card called
anything under 80 borderline until it was fixed; the jobs table showed every scored row at all.
Each was defensible alone and together they told the user three different stories about the same
job — sent to Telegram, marked "do not get excited", and buried in a page of noise.

It lives in `libs` because `services` may not import `services`, and the alternative was three
constants that start equal and drift. That is not a hypothetical: two of the three had already
drifted before this file existed.

Matches the reference's `min_score_for_cv` — the score at which it will build a CV without being
asked. So the meaning is "worth acting on", not "exceptional".
"""
from __future__ import annotations

DEFAULT_CUTOFF = 60.0

# Below this a job is not worth keeping in a per-user feed at all. The reference's
# `min_score_to_keep`; retained here because the scorer's `_decide` needs a floor for
# "needs-review" and the number should not be invented separately in two services.
DEFAULT_KEEP = 45.0
