"""One-off (2026-07-30): move migrated profile .md files out of `user_documents`.

The account migration stored the reference instance's profile files — `01-candidate-profile.md`,
`02-behavioral-profile.md`, `07-interview-prep.md`, `*_context_CLAUDE.md` — as uploaded documents,
so they appeared on the Documents page as if the user had uploaded them, and every processing run
read them as source material. They are platform files: they belong in `user_profile_files`, where
the setup chat maintains them.

Ruben's five are byte-duplicates of rows already in his profile store (moved by an earlier pass),
so they are deleted. Andras' are the only copy, so they move. `03-writing-style.md` is
platform-shared (`libs/profiles` EXCLUDED) and is deleted with the rest — the platform's copy is
the live one.

Run on the box:

    cd ~/job-search-app && set -a && . deploy/.env && set +a && \
    DATABASE_URL="postgresql+psycopg://jobsearch:${POSTGRES_PASSWORD}@127.0.0.1:5432/jobsearch" \
    .venv/bin/python scripts/move_md_documents.py

Every touched row is dumped to the temp directory as `docs_fix_backup.json` first.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text

MOVES = {  # document filename -> profile file name
    "01-candidate-profile.md": "01-candidate-profile.md",
    "02-behavioral-profile.md": "02-behavioral-profile.md",
    "07-interview-prep.md": "07-interview-prep.md",
    "ruben_context_CLAUDE.md": "context.md",
    "andras_context_CLAUDE.md": "context.md",
}
DROP_ONLY = {"03-writing-style.md"}


def main() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT d.id, d.user_id, d.filename, d.text FROM user_documents d "
            "WHERE d.filename = ANY(:names)"),
            {"names": list(MOVES) + list(DROP_ONLY)}).all()

        backup = [{"id": r.id, "user_id": str(r.user_id), "filename": r.filename, "text": r.text}
                  for r in rows]
        backup_path = Path(tempfile.gettempdir()) / "docs_fix_backup.json"
        backup_path.write_text(json.dumps(backup), encoding="utf-8")
        print(f"backed up {len(backup)} rows to {backup_path}")

        for r in rows:
            target = MOVES.get(r.filename)
            if target:
                existing = conn.execute(text(
                    "SELECT length(content) FROM user_profile_files "
                    "WHERE user_id = :uid AND name = :name"),
                    {"uid": r.user_id, "name": target}).scalar()
                if existing:
                    print(f"skip write {r.filename} -> {target}: profile store already has "
                          f"{existing} chars (the document was the duplicate)")
                else:
                    updated = conn.execute(text(
                        "UPDATE user_profile_files SET content = :content, updated_at = now() "
                        "WHERE user_id = :uid AND name = :name"),
                        {"content": r.text, "uid": r.user_id, "name": target}).rowcount
                    if not updated:
                        conn.execute(text(
                            "INSERT INTO user_profile_files (user_id, name, kind, content, "
                            "updated_at) VALUES (:uid, :name, :kind, :content, now())"),
                            {"uid": r.user_id, "name": target,
                             "kind": "context" if target == "context.md" else "skill",
                             "content": r.text})
                    print(f"moved {r.filename} -> profile store as {target}")
            conn.execute(text("DELETE FROM user_documents WHERE id = :id"), {"id": r.id})
            print(f"removed document row {r.id} ({r.filename})")


if __name__ == "__main__":
    main()
