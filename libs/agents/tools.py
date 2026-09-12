"""What the setup agent can actually do: read documents, read and write profile files."""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import UserDocument
from libs.labels import bump_generation
from libs.profiles import WRITEABLE
from libs.profiles.repository import ProfileFiles

logger = logging.getLogger(__name__)



class SetupTools:
    """The agent's four tools, bound to one user's session."""

    def __init__(self, session: Session, user_id: _uuid.UUID):
        self._s = session
        self._user = user_id
        self._files = ProfileFiles(session, user_id)
        # Which scoring-relevant files this run touched.
        self.scoring_edits: list[str] = []

    def run(self, name: str, arguments: dict) -> str:
        """Dispatch one tool call."""
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
        # Returned whole. The only ceiling is `libs.documents.MAX_TEXT_CHARS`, applied once when the
        # upload was stored — a second cap here cut the text again, further, and told the model rather
        # than the person who could have shortened the file.
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

        # The rubric has to reach the column the labeller reads.
        if name == "label.md":
            self._sync_rubric(content)

        # Audited per call: each one is a write to somebody's profile.
        logger.info("setup: %s wrote %s (%d chars)", self._user, name, len(content))
        return f"Saved {name} ({len(content):,} chars)."

    def _sync_rubric(self, content: str) -> None:
        """Mirror the written rubric onto the user row, and version it."""
        from libs.db.models import User  # noqa: PLC0415 - avoids an import cycle at module load
        from libs.scoring.rubric import profile_version  # noqa: PLC0415

        user = self._s.get(User, self._user)
        if user is None or (user.rubric_md or "") == content:
            return
        changed = bool(user.rubric_md)
        user.rubric_md = content
        user.profile_version = profile_version(content)
        if changed:
            # One place decides what a new generation means for the labels already held.
            bump_generation(self._s, user)
        logger.info("setup: %s rubric synced (%d chars, generation %d)",
                    self._user, len(content), user.profile_generation)
