"""Port source adapters from the reference instance, applying the changes multi-tenancy forces."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Adapters that are pure HTTP + parsing; `ats.py` is not one and is excluded.
ADAPTERS = ("http_apis.py", "hiringcafe.py", "vcboards.py", "jobspy_source.py")

HEADER = '''"""{original}

--- ported from the reference instance, {date} ---

Lifted from `job-search/pipeline/sources/{name}` by
`services/harvester/sources/port_from_reference.py`. Parsing, pagination and per-source quirks are
unchanged on purpose — they are the tuned part, and a faithful copy stays diffable against the
original.

The one systematic change: this module no longer constructs `httpx.Client` itself. Every client
comes from `services.harvester.fetch`, which is what keeps "whose credential paid for this fetch"
answerable, and what stops an ambient `HTTP_PROXY` silently routing the shared harvest through one
user's proxy.
"""
'''


def transform(source: str, name: str, date: str) -> tuple[str, list[str]]:
    """Apply the transformation. Returns the new text and a list of what changed."""
    changes: list[str] = []

    # 1. Every client comes from the one factory.
    client_calls = len(re.findall(r"httpx\.Client\(", source))
    if client_calls:
        source = re.sub(
            r"httpx\.Client\(\s*headers=_UA\s*,\s*timeout=_TIMEOUT\s*,\s*"
            r"follow_redirects=True\s*\)",
            "make_shared_client()", source)
        # Everything else keeps its arguments verbatim and still comes from the factory.
        source = re.sub(r"httpx\.Client\(", "make_shared_client(", source)
        # The factory supplies timeout and follow_redirects, and _TIMEOUT no longer exists.
        source = re.sub(r"(make_shared_client\([^)]*?),\s*timeout=_TIMEOUT", r"\1", source)
        changes.append(f"{client_calls} httpx.Client(...) -> make_shared_client(...)")

    # 2. Re-point imports at the new packages.
    source = source.replace("from .base import JobLink",
                            "from services.harvester.sources.base import JobLink")
    source = re.sub(r"from \.\.lib\.\w+ import ", "from libs.corpus.filters import ", source)
    def _rewrite_lib_import(match: re.Match) -> str:
        # `paths` and `store` do not survive the port: storage goes through repositories now.
        kept = sorted(x.strip() for x in match.group(1).split(",")
                      if x.strip() and x.strip() not in {"paths", "store"})
        if not kept:
            return "# (dropped: `paths` and `store` do not survive the port)"
        return "from libs.corpus.filters import " + ", ".join(kept)

    source = re.sub(r"from \.\.lib import ([^\n]+)", _rewrite_lib_import, source)

    # 3. Import the factory, after the last stdlib/third-party import block.
    if "make_shared_client" in source and "from services.harvester.fetch import" not in source:
        source = source.replace(
            "from services.harvester.sources.base import JobLink",
            "from services.harvester.fetch import make_shared_client\n"
            "from services.harvester.sources.base import JobLink", 1)
        changes.append("added the make_shared_client import")

    # 4. Parse third-party XML with defusedxml.
    if "xml.etree.ElementTree" in source:
        source = source.replace(
            "import xml.etree.ElementTree as ET",
            "import defusedxml.ElementTree as ET   # not stdlib ET: these parse third-party feeds")
        changes.append("XML parsing hardened with defusedxml")

    # 5. Replace the module docstring with one that records the provenance.
    match = re.match(r'^"""(.*?)"""\n', source, re.DOTALL)
    if match:
        source = HEADER.format(original=match.group(1).rstrip(), name=name, date=date) \
            + source[match.end():]
        changes.append("recorded provenance in the docstring")

    return source, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", default=r"d:\Projects\job-search")
    ap.add_argument("--check", action="store_true",
                    help="report what would change without writing")
    ap.add_argument("--date", default="2026-07-27")
    args = ap.parse_args()

    src_dir = Path(args.reference) / "pipeline" / "sources"
    if not src_dir.is_dir():
        print(f"reference sources not found at {src_dir}", file=sys.stderr)
        return 1

    out_dir = Path(__file__).parent
    for name in ADAPTERS:
        original = src_dir / name
        if not original.exists():
            print(f"  SKIP {name}: not present in the reference", file=sys.stderr)
            continue
        ported, changes = transform(original.read_text(encoding="utf-8"), name, args.date)
        target = out_dir / name
        print(f"  {name}: {', '.join(changes) or 'no changes'}")
        if not args.check:
            target.write_text(ported, encoding="utf-8")

    if args.check:
        print("\n--check: nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
