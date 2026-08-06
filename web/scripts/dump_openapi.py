"""Write the API's OpenAPI document to `web/openapi.json`."""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# The app reads these at import time.
os.environ.setdefault("ACCESS_TOKEN_SECRET", "generate-openapi-" + "x" * 24)
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/none")


def main() -> int:
    from services.api.app import app  # noqa: PLC0415 - after the environment is set

    target = pathlib.Path(__file__).resolve().parents[1] / "openapi.json"
    # Sorted and newline-normalised: an unstable dump produces a diff on every run.
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(app.openapi()['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
