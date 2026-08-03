"""What the setup agent can actually do: read documents, read and write profile files.

Four functions, and the list is deliberately short. `/setup` uses Read, Write, Glob and Edit and
nothing else — no bash, no web, no ability to reach outside this user's own rows. An agent
interviewing somebody about their career has no business fetching a URL, and a tool that exists is
a tool that will eventually be called.

**Every call is scoped to one user.** The executor is constructed with a `user_id` and every query
carries it; there is no code path here that can be handed a name and reach another account's file.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import UserDocument
from libs.profiles import SEED_FILES
from libs.profiles.repository import ProfileFiles

logger = logging.getLogger(__name__)

# Truncation guard for one document. The whole set is capped by the caller; this stops a single
# 300-page PDF from filling the window in one tool result.
MAX_DOCUMENT_CHARS = 60_000

# The names a file may be written to. A model that invents `08-references.md` would create a file
# nothing reads, and the user would never know the section it wrote went nowhere.
WRITEABLE = {name for name, _kind in SEED_FILES}


class SetupTools:
    """The agent's four tools, bound to one user's session."""

    def __init__(self, session: Session, user_id: _uuid.UUID):
        self._s = session
        self._user = user_id
        self._files = ProfileFiles(session, user_id)
        # Which scoring-relevant files this run touched. The caller turns this into the retrain
        # counter — the agent never triggers a relabel itself (`REBUILD_PLAN.md`).
        self.scoring_edits: list[str] = []

    def run(self, name: str, arguments: dict) -> str:
        """Dispatch one tool call. Returns whatever the model should see as the result.

        Errors come back as text rather than exceptions: a model that asked for a document that
        does not exist should be told so and try another, not have the conversation end.
        """
        try:
            handler = {
                "list_documents": self._list_documents,
                "read_document": self._read_document,
                "list_profile_files": self._list_profile_files,
                "read_profile_file": self._read_profile_file,
                "write_profile_file": self._write_profile_file,
            }[name]
        except KeyError:
            return f"There is no tool called {name!r}."

        try:
            return handler(**arguments)
        except TypeError as exc:
            return f"Wrong arguments for {name}: {exc}"

    # ── documents the user uploaded ───────────────────────────────────────────────────────────

    def _list_documents(self) -> str:
        rows = self._s.execute(
            select(UserDocument.filename, UserDocument.kind, UserDocument.chars)
            .where(UserDocument.user_id == self._user)
            .order_by(UserDocument.uploaded_at)).all()
        if not rows:
            return ("No documents uploaded. Interview the user directly instead of reading — "
                    "this is Path C, not Path A.")
        return "\n".join(f"- {name} ({kind}, {chars:,} chars)" for name, kind, chars in rows)

    def _read_document(self, name: str) -> str:
        text = self._s.scalar(
            select(UserDocument.text)
            .where(UserDocument.user_id == self._user, UserDocument.filename == name))
        if text is None:
            return f"No document called {name!r}. Call list_documents to see what there is."
        if len(text) > MAX_DOCUMENT_CHARS:
            return text[:MAX_DOCUMENT_CHARS] + f"\n\n[truncated at {MAX_DOCUMENT_CHARS:,} chars]"
        return text

    # ── the profile files ─────────────────────────────────────────────────────────────────────

    def _list_profile_files(self) -> str:
        rows = self._files.listing()
        if not rows:
            return "This account has no profile files yet."
        return "\n".join(
            f"- {row['name']} ({row['kind']}, "
            f"{'filled' if row['filled'] else 'still the empty scaffold'})" for row in rows)

    def _read_profile_file(self, name: str) -> str:
        content = self._files.read(name)
        if content is None:
            return f"No profile file called {name!r}. Call list_profile_files to see what there is."
        return content

    def _write_profile_file(self, name: str, content: str) -> str:
        if name not in WRITEABLE:
            return (f"{name!r} is not one of this platform's profile files, so nothing would read "
                    f"it. Write to one of: {', '.join(sorted(WRITEABLE))}.")
        if not content.strip():
            return "Refused: that would empty the file. Write its full new content."

        scoring = self._files.write(name, content)
        if scoring and name not in self.scoring_edits:
            self.scoring_edits.append(name)

        # **The rubric has to reach the column the labeller reads.** `label.md` is written here as
        # a profile file, and everything downstream -- cold start, relabelling, which labels a
        # training run may use -- reads `users.rubric_md`, which only the superseded capture flow
        # ever populated. So an account set up by the conversation had a finished rubric and a
        # labeller that answered "confirm_profile" forever: no pool, no labels, no model, and
        # nothing anywhere saying why.
        if name == "label.md":
            self._sync_rubric(content)

        # Audited per call because each one is a write to somebody's profile, and "when did my
        # rubric change" is a question support will be asked.
        logger.info("setup: %s wrote %s (%d chars)", self._user, name, len(content))
        return f"Saved {name} ({len(content):,} chars)."

    def _sync_rubric(self, content: str) -> None:
        """Mirror the written rubric onto the user row, and version it.

        `profile_version` is `sha256(rubric_md)[:8]` and scopes which labels a training run may use,
        so it must move with the text. `profile_generation` advances only when the rubric actually
        changed: it is what marks existing labels stale, and bumping it on an identical rewrite
        would send a user back through relabelling they did not need.
        """
        from libs.db.models import User  # noqa: PLC0415 - avoids an import cycle at module load
        from libs.scoring.rubric import profile_version  # noqa: PLC0415

        user = self._s.get(User, self._user)
        if user is None or (user.rubric_md or "") == content:
            return
        changed = bool(user.rubric_md)
        user.rubric_md = content
        user.profile_version = profile_version(content)
        if changed:
            user.profile_generation += 1
        logger.info("setup: %s rubric synced (%d chars, generation %d)",
                    self._user, len(content), user.profile_generation)
