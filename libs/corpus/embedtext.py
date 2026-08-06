"""The text a job is embedded from."""
from __future__ import annotations

# The reference's cap.
MAX_CHARS = 4_000

# Written into `EmbedderVersion.version`: a row embedded under one mode is not scorable under another.
DEFAULT_MODE = "title_desc"


def build_text(title: str | None, description: str | None, *, mode: str = DEFAULT_MODE) -> str:
    """The text to embed for one job."""
    parts = [description] if mode == "desc" else [title, description]
    return "\n".join(part for part in parts if part)[:MAX_CHARS]


def embedding_version(version, *, mode: str = DEFAULT_MODE) -> str:
    """The embedder's own version string, with the text mode folded in."""
    raw = getattr(version, "version", version)
    return f"{raw}|text={mode}"
