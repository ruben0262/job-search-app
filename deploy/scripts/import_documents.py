"""Import a directory of a user's real documents into their account.

    python deploy/scripts/import_documents.py --email you@example.com --dir /tmp/docs --dry-run
    python deploy/scripts/import_documents.py --email you@example.com --dir /tmp/docs

For migrating the reference instances' owners, whose documents live in a folder rather than behind
the upload form. Everything the form does, this does: the same extractor, the same content hash, the
same replace-on-re-upload, and **the file is never stored** — only the text
(`libs/documents/extract.py`).

**The kind is inferred from the filename and can be overridden**, because the guess is right most of
the time and wrong in a way that matters occasionally: kind drives nothing structural, but it is
what lets a user see at a glance that their CV is in there.

Marks `documents_changed_at`, so the dashboard tells the account it has something new to process
rather than leaving the import invisible.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import func, select

from libs.db.models import User, UserDocument
from libs.db.session import session_scope
from libs.documents import SUPPORTED, UnsupportedDocument, extract_text
from libs.obs import configure

# Filename fragment -> document kind, first match wins. Ordered so the more specific patterns are
# tested before the general ones — "cover_letter" must not be read as a CV because it contains "cv"
# nowhere but "letter" everywhere.
PATTERNS = (
    ("cover", "cover_letter"),
    ("reference", "reference"),
    ("recommendation", "reference"),
    ("diploma", "diploma"),
    ("degree", "diploma"),
    ("transcript", "diploma"),
    ("linkedin", "linkedin"),
    ("context", "history"),
    ("history", "history"),
    ("motivation", "history"),
    ("achievement", "achievement"),
    ("cv", "cv"),
    ("resume", "cv"),
)


def infer_kind(path: Path | str, root: Path | None = None) -> str:
    """Guess from the **folder first, then the filename**.

    The reference instances put the meaning in the directory — `documents/cv/`,
    `documents/linkedin/`, `documents/references/` — and the filenames inside are whatever the file
    was called when it was downloaded. Reading only the name got `Profile (1).pdf` wrong: it is a
    LinkedIn export sitting in `linkedin/`, and nothing in its name says so.
    """
    path = Path(path)
    parents = ""
    if root is not None:
        try:
            parents = " ".join(part.lower() for part in path.relative_to(root).parts[:-1])
        except ValueError:
            parents = ""
    for haystack in (parents, path.name.lower()):
        match = next((kind for fragment, kind in PATTERNS if fragment in haystack), None)
        if match:
            return match
    return "other"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--kind", help="force one kind for every file instead of inferring")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be imported, write nothing")
    args = parser.parse_args(argv)

    configure("tools")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    root = Path(args.dir)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    # Sorted so a re-run imports in the same order, and so the output is readable.
    files = sorted(path for path in root.rglob("*")
                   if path.is_file() and path.suffix.lower() in SUPPORTED)
    if not files:
        print(f"nothing importable in {root} — supported: {', '.join(sorted(SUPPORTED))}")
        return 0

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"no such account: {args.email}", file=sys.stderr)
            return 2

        imported = skipped = replaced = 0
        for path in files:
            kind = args.kind or infer_kind(path, root)
            try:
                extracted = extract_text(path.name, path.read_bytes())
            except UnsupportedDocument as exc:
                # Named rather than counted: a CV that failed to read is the one file whose absence
                # actually matters, and a bare "3 skipped" hides it.
                print(f"  SKIP  {path.name}: {exc}")
                skipped += 1
                continue

            existing = session.scalar(
                select(UserDocument).where(UserDocument.user_id == user.id,
                                           UserDocument.text_sha256 == extracted.sha256))
            state = "replace" if existing else "import"
            print(f"  {state:7} {kind:12} {extracted.chars:>7,} chars  {path.name}"
                  + ("  (truncated)" if extracted.truncated else ""))
            if args.dry_run:
                continue

            if existing is not None:
                existing.kind, existing.filename = kind, path.name
                replaced += 1
            else:
                session.add(UserDocument(user_id=user.id, kind=kind, filename=path.name,
                                         text=extracted.text, text_sha256=extracted.sha256,
                                         chars=extracted.chars))
                imported += 1

        if args.dry_run:
            print(f"\n--dry-run: {len(files)} file(s) considered, nothing written")
            return 0

        # What makes the dashboard say "you have added documents, process them to update your
        # profile" rather than leaving the import invisible.
        user.documents_changed_at = func.now()
        print(f"\n{imported} imported, {replaced} replaced, {skipped} skipped for {args.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
