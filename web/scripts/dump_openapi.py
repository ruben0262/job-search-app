"""Write the API's OpenAPI document to `web/openapi.json`.

    python web/scripts/dump_openapi.py

The TypeScript types are generated from this, and **the generated file is committed** so CI can
diff it (`web/README.md`). An unexpected diff means somebody changed a response shape without
meaning to — which is exactly the change that would otherwise surface as a runtime `undefined` in
front of a user.

Imports the app rather than calling a running server: generation must not depend on the API being
up, or the types silently stop being regenerated the first time it is down.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# The app reads these at import time. Values are irrelevant — nothing here makes a request — but
# their absence would stop the import, which would make schema generation depend on a configured
# environment for no reason.
os.environ.setdefault("ACCESS_TOKEN_SECRET", "generate-openapi-" + "x" * 24)
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/none")


def main() -> int:
    from services.api.app import app  # noqa: PLC0415 - after the environment is set

    target = pathlib.Path(__file__).resolve().parents[1] / "openapi.json"
    # `sort_keys` and a trailing newline so the committed file is stable: an unstable dump produces
    # a diff on every run and nobody reads a diff that is always noisy.
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(app.openapi()['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
