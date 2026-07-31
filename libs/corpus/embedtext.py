"""The text a job is embedded from. One definition, used by everything that embeds or scores.

**Port of the reference's `build_text`.** The platform embedded `description` alone, which is that
function's non-default `desc` mode, and the difference is not cosmetic:

- **The title is the strongest single statement of what a job is.** "Senior Mechanical Engineer"
  and "Backend Engineer" can have similar-looking descriptions — benefits, culture, process — and
  differ entirely in the one line that decides relevance. The reference puts it first.
- **First matters because the encoder truncates.** `bge-base` takes 512 tokens, roughly 2,000
  characters. Descriptions on some boards average 6,662, so two thirds is discarded and what
  survives is whatever opened the posting. A title appended at the end would be cut off; a title at
  the front never is.

## What is deliberately not in it

No company, no location, no salary. The reference states the reason and it is about generalisation
rather than tidiness: a model that has seen the employer name learns employers, scores unseen
companies badly, and quietly encodes geography as fit. The job's *work* is the only thing that
should move the number.

## Changing this invalidates every vector

The text is the input; a different input is a different embedding, and a model trained on one
cannot read the other. So the mode is part of `EmbedderVersion.version`, which `score_rows` already
checks per row and refuses on mismatch — a stale vector raises rather than scoring wrong. The cost
of a change here is a re-embed and a retrain, and that is the honest price rather than a reason to
leave it.
"""
from __future__ import annotations

# The reference's cap. Well past what the encoder reads, so it is not the truncation that matters —
# it bounds the payload sent over the wire for a posting that inlines an entire employee handbook.
MAX_CHARS = 4_000

# Written into `EmbedderVersion.version`, so a corpus row embedded under one mode cannot be scored
# by a model trained under another. Without it the change is silent and the failure is a confidently
# wrong score.
DEFAULT_MODE = "title_desc"


def build_text(title: str | None, description: str | None, *, mode: str = DEFAULT_MODE) -> str:
    """The text to embed for one job.

    `mode` exists because the reference has it and an A/B between the two is a real experiment
    somebody may want to run — not because either is configurable per user. It belongs to the
    model, and the model records which one it was trained under.
    """
    parts = [description] if mode == "desc" else [title, description]
    return "\n".join(part for part in parts if part)[:MAX_CHARS]


def embedding_version(version, *, mode: str = DEFAULT_MODE) -> str:
    """The embedder's own version string, with the text mode folded in.

    `score_rows` compares this per row and refuses a mismatch, so changing the mode makes every
    existing vector visibly stale rather than silently wrong. That refusal is the whole point: two
    vectors of the same width from different input text are indistinguishable by inspection.
    """
    raw = getattr(version, "version", version)
    return f"{raw}|text={mode}"
