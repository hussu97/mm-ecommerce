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
    "products": "/products",
    "modifier_options": "/modifier_options",
}

# Foodics exposes the list collections above, but its console does not expose
# the three global ingredient collections to this account. The detail resources
# do carry the authoritative `ingredients` relationship (including each pivot
# quantity/yield), so recipe extraction deliberately fans out through those
# read-only detail routes instead of mistaking an empty collection for no
# recipes.
RECIPE_OWNERS: dict[str, tuple[str, str, str]] = {
    "inventory_item_ingredients": (
        "inventory_items",
        "inventory_item_id",
        "/inventory_items",
    ),
    "product_ingredients": ("products", "product_id", "/products"),
    "modifier_option_ingredients": (
        "modifier_options",
        "modifier_option_id",
        "/modifier_options",
    ),
}
_DETAIL_CONCURRENCY = 8

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


def _ingredient_lines(
    *, owner_id: str, owner_field: str, detail: dict[str, Any]
) -> list[dict[str, Any]]:
    """Flatten one Foodics detail response without inventing a recipe line.

    The console nests ingredients under the owner, with amounts on `pivot`.
    Keeping that source ID and pivot payload in the snapshot means the staging
    command can be rerun/reviewed without an unstable name match.
    """
    lines: list[dict[str, Any]] = []
    for ingredient in detail.get("ingredients") or []:
        if not isinstance(ingredient, dict) or not ingredient.get("id"):
            continue
        pivot = ingredient.get("pivot") or {}
        lines.append(
            {
                "id": f"{owner_id}:{ingredient['id']}",
                owner_field: owner_id,
                "ingredient_id": str(ingredient["id"]),
                "quantity": pivot.get("quantity"),
                "yield_percentage": pivot.get("yield_percentage"),
                "inactive_in_order_types": pivot.get("inactive_in_order_types"),
                "source_pivot": pivot,
            }
        )
    return lines


async def _extract_recipe_lines(data: dict[str, Any], errors: dict[str, str]) -> None:
    """Read the console detail payload for every owner declaring ingredients."""
    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def read_owner(
        owner: dict[str, Any], owner_field: str, resource: str
    ) -> list[dict[str, Any]]:
        owner_id = str(owner["id"])
        async with semaphore:
            payload = await fp.provider._call(  # noqa: SLF001 - console detail API
                "GET", fp._GETTING, params={"url": f"{resource}/{owner_id}"}
            )
        detail = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(detail, dict):
            raise fp.FoodicsError(
                f"Foodics returned no detail for {resource}/{owner_id}"
            )
        return _ingredient_lines(
            owner_id=owner_id, owner_field=owner_field, detail=sanitize(detail)
        )

    for destination, (source, owner_field, resource) in RECIPE_OWNERS.items():
        owners = [
            row
            for row in data.get(source, [])
            # List payloads omit `has_ingredients` for this Foodics account;
            # only the detail payload is authoritative. We must therefore read
            # every valid owner rather than treating omission as a false flag.
            if isinstance(row, dict) and row.get("id")
        ]
        results = await asyncio.gather(
            *(read_owner(owner, owner_field, resource) for owner in owners),
            return_exceptions=True,
        )
        failed = [result for result in results if isinstance(result, Exception)]
        if failed:
            # A partial recipe graph is dangerous: it can look valid while
            # silently omitting an owner. Do not stage any of this snapshot.
            errors[destination] = "; ".join(str(error) for error in failed[:3])
            data[destination] = []
            continue
        data[destination] = [line for result in results for line in result]


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
    if not errors:
        await _extract_recipe_lines(data, errors)
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
