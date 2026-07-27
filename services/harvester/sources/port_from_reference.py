"""Port source adapters from the reference instance, applying the changes multi-tenancy forces.

    python services/harvester/sources/port_from_reference.py [--reference d:/Projects/job-search]
    python services/harvester/sources/port_from_reference.py --check

**This is a record of how the adapters got here, not a build step.** It runs once per adapter and
its output is committed. Kept in the tree because the transformation it applies is the interesting
part of the port, and re-deriving it later from a diff would be far harder than reading it.

The adapters are ~3,500 lines of source-specific parsing that is genuinely "lifted nearly as-is"
(`CODE_STRUCTURE.md` §3). Retyping them by hand would introduce transcription errors into code whose
correctness depends on details no test here can check — a JSON path, a date format, a pagination
parameter. A scripted transformation changes exactly what it says it changes.

## What it changes, and why each is forced

1. **`httpx.Client(...)` → `make_shared_client(...)`.** Every adapter builds its own client — 20+
   call sites. Harmless single-tenant, because none of these boards need a proxy. Here it would
   break the invariant `fetch.py` exists to hold (one factory, so "whose credential paid for this"
   has one answer) **and** silently restore `trust_env=True` on every one of them, letting an
   ambient `HTTP_PROXY` route the whole shared harvest through one user's credential.
2. **`_UA` / `_TIMEOUT` arguments dropped** where the factory already supplies them, so there is one
   definition of the platform's user agent rather than one per adapter.
3. **`from .base import JobLink` and relative imports** re-pointed at the new package.

## What it deliberately does NOT change

Parsing, pagination, date handling, per-source quirks, and every comment explaining them. Those are
the tuned parts. If a source's JSON path is wrong here it was wrong there, and that is the right
trade: a faithful copy is diffable against the original, an "improved" one is not.

`ats.py` is **excluded** — it is the one adapter with real store integration (tokens and ETags), and
it is rewritten by hand against `AtsRepository` rather than transformed.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Adapters that are pure HTTP + parsing. `ats.py` is excluded: see the module docstring.
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
    #
    # ⚠ **Only `headers=_UA` may be dropped** — that is the platform default the factory already
    # supplies. Any other headers argument is source-specific and load-bearing: an early version of
    # this script also matched `headers=ua` and silently discarded RemoteOK's custom User-Agent,
    # which exists precisely because RemoteOK blocks the generic Mozilla one. The source would have
    # returned nothing, with a comment two lines up explaining why. Caught by ruff flagging the
    # now-unused variable, which is luck rather than design — so the rule is narrow now.
    client_calls = len(re.findall(r"httpx\.Client\(", source))
    if client_calls:
        source = re.sub(
            r"httpx\.Client\(\s*headers=_UA\s*,\s*timeout=_TIMEOUT\s*,\s*"
            r"follow_redirects=True\s*\)",
            "make_shared_client()", source)
        # Everything else keeps its arguments verbatim and still comes from the factory.
        source = re.sub(r"httpx\.Client\(", "make_shared_client(", source)
        # The factory supplies timeout and follow_redirects; passing them again is harmless but
        # noisy, and _TIMEOUT no longer exists as a shared constant in some modules.
        source = re.sub(r"(make_shared_client\([^)]*?),\s*timeout=_TIMEOUT", r"\1", source)
        changes.append(f"{client_calls} httpx.Client(...) -> make_shared_client(...)")

    # 2. Re-point imports at the new packages.
    #
    # The reference's `pipeline/lib/` helpers move to `libs/corpus/filters` rather than under the
    # service, because more than one service needs them: the harvester filters on recency at ingest,
    # and the scorer and notifier both render "how long ago" from the same timestamps
    # (CODE_STRUCTURE.md §3). A per-service copy would drift, and a job that reads 3 hours old in
    # the feed and 3 days old in a digest is the kind of inconsistency nobody reports as a bug.
    #
    # Both import spellings appear in the reference and both must be handled — `from ..lib import x`
    # and `from ..lib.module import x`. Missing the second form was caught by the modules simply
    # failing to import, which is the good case.
    source = source.replace("from .base import JobLink",
                            "from services.harvester.sources.base import JobLink")
    source = re.sub(r"from \.\.lib\.\w+ import ", "from libs.corpus.filters import ", source)
    def _rewrite_lib_import(match: re.Match) -> str:
        # `paths` and `store` do not survive the port: filesystem paths are gone, and storage goes
        # through repositories rather than a module-level store. When *every* name on the line is
        # one of those, the whole import must go — leaving `from x import` with nothing after it is
        # a syntax error, which is how this was caught.
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
    #
    # The only change here that is not mechanical, and the only one that alters behaviour on
    # purpose. Several adapters parse RSS/XML from job boards with stdlib ElementTree, which the
    # stdlib's own security documentation flags as vulnerable to entity-expansion ("billion
    # laughs") and quadratic blowup. Single-tenant on a laptop that is a shrug; here the harvester
    # shares a box with Postgres, and a hostile or merely broken feed exhausting memory takes the
    # platform down for everyone. defusedxml is a small pure-Python drop-in.
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
