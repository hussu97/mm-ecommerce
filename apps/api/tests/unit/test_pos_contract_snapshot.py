"""
Every POS endpoint an existing register build calls keeps its contract.

Terminals update over TestFlight at different times, so a build that predates
local-first checkout keeps calling `/pos/orders`, `/items`, `/payments`,
`/close`, `/send-to-kitchen`, `/void`, the heartbeat and the till endpoints
against a server that has moved on. Those builds decode with Swift `Codable`,
which ignores unknown keys but fails on a missing one, and send bodies that know
nothing of new fields. So, against a snapshot of the contract as it was before
local-first shipped (`tests/fixtures/pos_contract_snapshot.json`, taken from
main's OpenAPI document):

* no request gained a **required** field or parameter, at any depth;
* no request or response field was removed or changed type, at any depth;
* new optional fields are allowed — additions are the whole plan.

If a change here is intended, it is a breaking change for the fleet: ship it
behind a new endpoint or a build gate instead of editing the snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app as web_app

SNAPSHOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "pos_contract_snapshot.json"
)

#: The surface an older register build calls.
PREFIXES = (
    "/api/v1/pos/orders",
    "/api/v1/pos/kitchen",
    "/api/v1/tills",
    "/api/v1/devices/heartbeat",
    "/api/v1/devices/pair",
    "/api/v1/staff/pin-login",
)


def _ref(schema: dict) -> str | None:
    ref = schema.get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


def _sig(schema: dict, seen: set[str]) -> dict:
    """A comparable signature: types, formats, enums and ref names."""
    name = _ref(schema)
    if name:
        seen.add(name)
        return {"ref": name}
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            return {key: sorted((_sig(s, seen) for s in schema[key]), key=json.dumps)}
    out: dict = {"type": schema.get("type")}
    if "items" in schema:
        out["items"] = _sig(schema["items"], seen)
    if "enum" in schema:
        out["enum"] = sorted(map(str, schema["enum"]))
    if schema.get("format"):
        out["format"] = schema["format"]
    if isinstance(schema.get("additionalProperties"), dict):
        out["additional"] = _sig(schema["additionalProperties"], seen)
    return out


def _shape(schema: dict, seen: set[str]) -> dict:
    return {
        "required": sorted(schema.get("required") or []),
        "properties": {
            k: _sig(v, seen)
            for k, v in sorted((schema.get("properties") or {}).items())
        },
    }


def snapshot(doc: dict) -> dict:
    """The contract of every POS path in an OpenAPI document, with the
    component schemas it reaches stored once."""
    seen: set[str] = set()
    endpoints: dict = {}
    for path, item in sorted(doc["paths"].items()):
        if not path.startswith(PREFIXES):
            continue
        for method, op in sorted(item.items()):
            entry: dict = {
                "params": sorted(
                    [p["name"], p["in"], bool(p.get("required"))]
                    for p in op.get("parameters", [])
                ),
            }
            body = (op.get("requestBody") or {}).get("content", {})
            body = body.get("application/json", {}).get("schema")
            if body is not None:
                entry["request_required"] = bool(op["requestBody"].get("required"))
                entry["request"] = _sig(body, seen)
            for status, resp in sorted(op.get("responses", {}).items()):
                schema = resp.get("content", {}).get("application/json", {})
                if status.startswith("2") and "schema" in schema:
                    entry[f"response_{status}"] = _sig(schema["schema"], seen)
            endpoints[f"{method.upper()} {path}"] = entry

    schemas: dict = {}
    pending = sorted(seen)
    while pending:
        name = pending.pop()
        if name in schemas:
            continue
        before = set(seen)
        schemas[name] = _shape(doc["components"]["schemas"][name], seen)
        pending.extend(sorted(seen - before - set(schemas)))
    return {"endpoints": endpoints, "schemas": schemas}


class _Checker:
    def __init__(self, old: dict, new: dict):
        self.old, self.new = old, new
        self.problems: list[str] = []
        self._done: set[tuple[str, bool]] = set()

    def sig(self, where: str, old: dict, new: dict | None, *, request: bool) -> None:
        if new is None:
            self.problems.append(f"{where}: removed")
            return
        if "ref" in old:
            if new.get("ref") != old["ref"]:
                self.problems.append(f"{where}: was {old['ref']}, now {new}")
                return
            self.schema(old["ref"], request=request)
            return
        for key in ("anyOf", "oneOf", "allOf"):
            if key in old:
                if key not in new or len(new[key]) < len(old[key]):
                    self.problems.append(f"{where}: {key} changed")
                    return
                for a, b in zip(old[key], new[key]):
                    self.sig(where, a, b, request=request)
                return
        for key in ("type", "format", "enum"):
            if old.get(key) != new.get(key):
                self.problems.append(f"{where}: {key} {old.get(key)} → {new.get(key)}")
        for key in ("items", "additional"):
            if key in old:
                self.sig(f"{where}[]", old[key], new.get(key), request=request)

    def schema(self, name: str, *, request: bool) -> None:
        if (name, request) in self._done:
            return
        self._done.add((name, request))
        old = self.old["schemas"][name]
        new = self.new["schemas"].get(name)
        if new is None:
            self.problems.append(f"schema {name}: removed")
            return
        if request:
            added = set(new["required"]) - set(old["required"])
            if added:
                self.problems.append(f"schema {name}: new required fields {added}")
        for prop, sig in old["properties"].items():
            self.sig(
                f"{name}.{prop}", sig, new["properties"].get(prop), request=request
            )


def _check(before: dict, now: dict) -> list[str]:
    checker = _Checker(before, now)
    for key, old in before["endpoints"].items():
        new = now["endpoints"].get(key)
        if new is None:
            checker.problems.append(f"{key}: endpoint removed")
            continue
        old_params = {tuple(p[:2]): p[2] for p in old["params"]}
        new_params = {tuple(p[:2]): p[2] for p in new["params"]}
        for param, required in new_params.items():
            if required and not old_params.get(param):
                checker.problems.append(f"{key}: new required param {param}")
        for param in old_params:
            if param not in new_params:
                checker.problems.append(f"{key}: param {param} removed")
        if "request" in old:
            if new.get("request_required") and not old.get("request_required"):
                checker.problems.append(f"{key}: request body became required")
            checker.sig(f"{key} body", old["request"], new.get("request"), request=True)
        elif new.get("request_required"):
            checker.problems.append(f"{key}: a required request body appeared")
        for part in [k for k in old if k.startswith("response_")]:
            checker.sig(f"{key} {part}", old[part], new.get(part), request=False)
    return checker.problems


def test_no_existing_pos_endpoint_broke_its_contract():
    before = json.loads(SNAPSHOT.read_text())
    problems = _check(before, snapshot(web_app.openapi()))
    assert not problems, "\n".join(problems)


def test_the_checker_catches_a_breaking_change():
    """The guard itself: a removed response field and a new required request
    field are both reported."""
    before = json.loads(SNAPSHOT.read_text())
    broken = json.loads(json.dumps(before))
    broken["schemas"]["PosOrderResponse"]["properties"].pop("total")
    broken["schemas"]["PaymentRequest"]["required"].append("brand_new")
    problems = _check(before, broken)
    assert any("PosOrderResponse.total" in p for p in problems)
    assert any("brand_new" in p for p in problems)


def test_the_snapshot_covers_the_register_surface():
    before = json.loads(SNAPSHOT.read_text())["endpoints"]
    for key in (
        "POST /api/v1/pos/orders",
        "POST /api/v1/pos/orders/{order_id}/items",
        "POST /api/v1/pos/orders/{order_id}/payments",
        "POST /api/v1/pos/orders/{order_id}/close",
        "POST /api/v1/pos/orders/{order_id}/send-to-kitchen",
        "POST /api/v1/pos/orders/{order_id}/void",
        "POST /api/v1/devices/heartbeat",
        "POST /api/v1/tills/{till_id}/close",
        "POST /api/v1/tills/open",
    ):
        assert key in before, key
