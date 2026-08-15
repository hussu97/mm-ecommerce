"""
Write the API's OpenAPI document where the TypeScript codegen can see it.

The frontends' types used to be transcribed from `app/schemas` by hand — three
files, ~2,000 lines, drifting silently (the admin was missing three order
statuses, including the one its own refund button produces). The document this
script writes is the contract the apps generate their types from instead, so a
schema change that is not reflected in `packages/types` fails CI rather than
shipping as a mismatch.

Usage:
    python -m scripts.export_openapi            # write packages/types/openapi.json
    python -m scripts.export_openapi --check    # exit 1 if the committed file is stale

`--check` runs in the API's CI job, which has Python and no Node; the
TypeScript side has its own freshness check against this file. Between the two,
neither language can move without the other noticing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parents[3] / "packages" / "types" / "openapi.json"


def current_document() -> str:
    # Imported here so `--help`-style failures don't need the app importable.
    from app.main import app

    # Sorted keys + stable separators: the file is diffed by CI, so its byte
    # layout has to be a function of the schema alone, not of dict ordering.
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    document = current_document()
    if "--check" in sys.argv:
        on_disk = OUT_PATH.read_text() if OUT_PATH.exists() else ""
        if on_disk != document:
            sys.stderr.write(
                "packages/types/openapi.json is stale. The API's schemas "
                "changed without regenerating the shared types.\n"
                "Run: cd apps/api && python -m scripts.export_openapi "
                "&& pnpm --filter @mm/types generate\n"
            )
            return 1
        print("openapi.json is current.")
        return 0
    OUT_PATH.write_text(document)
    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
