"""Create a read-only, versioned Foodics inventory/recipe snapshot.

This command never mutates Foodics or MM. It uses the same authenticated console
session as the existing provider and writes a canonical JSON file for audited
review and staging.

    uv run python scripts/foodics/extract_inventory_snapshot.py snapshot.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.services.providers import foodics_provider as fp  # noqa: E402

RESOURCES: dict[str, str] = {
    "units": "/inventory_units",
    "inventory_categories": "/inventory_categories",
    "inventory_items": "/inventory_items",
    "inventory_item_ingredients": "/inventory_item_ingredients",
    "products": "/products",
    "product_ingredients": "/product_ingredients",
    "modifier_options": "/modifier_options",
    "modifier_option_ingredients": "/modifier_option_ingredients",
}

SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "password",
    "refresh_token",
    "secret",
    "session",
    "token",
}


def sanitize(value: Any) -> Any:
    """Remove credential-shaped fields before a provider payload reaches disk."""
    if isinstance(value, dict):
        return {
            key: sanitize(child)
            for key, child in value.items()
            if key.casefold() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [sanitize(child) for child in value]
    return value


def canonical_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(body.encode()).hexdigest()


async def extract() -> dict[str, Any]:
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, resource in RESOURCES.items():
        try:
            data[key] = sanitize(await fp.provider._list_all(resource, cap_pages=100))
        except fp.FoodicsError as exc:
            # Keep the successfully captured resources useful, but make a partial
            # capture impossible to mistake for a complete import source.
            data[key] = []
            errors[key] = str(exc)
    content_hash = canonical_hash(data)
    return {
        "schema_version": 1,
        "source": "foodics_console_read_only",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": content_hash,
        "complete": not errors,
        "errors": errors,
        "counts": {key: len(value) for key, value in data.items()},
        "data": data,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    snapshot = await extract()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({key: snapshot[key] for key in ("complete", "counts", "errors")}))
    if not snapshot["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
