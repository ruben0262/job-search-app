"""Shadow scoring — having the judge grade the model, on a sample.

The local model is trained on the judge's labels and then serves scores on its own for months. The
question nobody can answer without this is whether it still agrees with the judge: a model that has
drifted produces confident numbers that no longer mean what they meant, and there is no error, no
alert, and no way to notice from the outside.

So a sample of what the model scores is also sent to the judge, and both numbers are kept side by
side. The disagreements are the interesting rows twice over — they say when a retrain is due, and
they are exactly the jobs worth asking the user about.

## Sampling is deterministic, and that is the whole design

`sha256(user_id:job_id) % 100 < rate`. Three properties fall out of it and every one of them is
load-bearing:

- **A re-run does not re-spend.** The queue replays jobs; a random sample would draw a new 10% each
  time and the bill would grow with the retries rather than with the work.
- **It is stable across processes.** Python's own `hash()` is salted per interpreter, so the same
  job would be sampled in one worker and not in another — the bug this docstring exists to prevent.
- **It is unbiased in the way that matters.** The digest of a uuid and an id has no relationship to
  score, source, or recency, so the sample is not quietly all LinkedIn or all high scorers.

## 10% is a guess, and should be called one until there is data

Nobody has measured the right rate. The way to find it is to collect pairs at 10%, then resample
the stored pairs at 5/10/25/50/100% and measure how quickly a known drift is detected at each — the
knee in that curve is the answer. Until then this is a defensible starting point and nothing more.
"""
from __future__ import annotations

import uuid as _uuid
from hashlib import sha256

# Percent of scored jobs also sent to the judge. Counts against the user's daily label quota, which
# is why it is a small number and why the sampling has to be stable.
DEFAULT_SAMPLE_RATE = 10

# How far the two numbers may differ before the pair counts as a disagreement. The scores are
# 0–100 and neither instrument is precise to a point, so a narrow band would call ordinary noise a
# drift and a wide one would miss a model that has genuinely moved.
AGREEMENT_TOLERANCE = 15


def is_sampled(user_id: _uuid.UUID | str, job_id: int, *, rate: int = DEFAULT_SAMPLE_RATE) -> bool:
    """Whether this (user, job) pair is in the shadow sample. Deterministic and stable.

    `rate` is a percentage: 0 disables sampling, 100 shadows everything.
    """
    if rate <= 0:
        return False
    if rate >= 100:
        return True
    digest = sha256(f"{user_id}:{job_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100 < rate


def agrees(model_score: float | None, judge_score: float | None, *,
           tolerance: int = AGREEMENT_TOLERANCE) -> bool | None:
    """Whether the two scores tell the same story. `None` when either is missing.

    None rather than False for a missing value: "we never asked" and "they disagreed" are different
    facts, and collapsing them would make a quiet outage look like a drifting model.
    """
    if model_score is None or judge_score is None:
        return None
    return abs(float(model_score) - float(judge_score)) <= tolerance
