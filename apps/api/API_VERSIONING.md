# API Versioning Strategy

## Current State

All routes are under `/api/v1/`, mounted in `app/main.py`:

```python
app.include_router(api_router, prefix="/api/v1")
```

The v1 router is defined in `app/api/v1/router.py` and aggregates all domain routers.

---

## When to Bump to v2

**Requires a version bump (breaking changes):**
- Removing an existing field from a response schema
- Renaming a field (e.g., `order_number` → `reference_id`)
- Changing a field's type (e.g., string → integer)
- Removing an endpoint entirely
- Changing the semantics of an existing parameter (e.g., a flag that previously meant X now means Y)
- Changing authentication requirements on an existing endpoint

**Does NOT require a version bump:**
- Adding a new optional field to a response (additive changes are safe)
- Adding a new endpoint
- Bug fixes that correct clearly wrong behavior
- Performance improvements with identical contracts
- New optional query/body parameters with default values

---

## How to Add v2

1. Create `app/api/v2/` directory mirroring the v1 structure
2. Copy and modify only the routers that have breaking changes; import unchanged routers from v1
3. Create `app/api/v2/router.py` aggregating v2 routers
4. Mount in `main.py`:
   ```python
   from app.api.v2.router import api_router_v2
   app.include_router(api_router_v2, prefix="/api/v2")
   ```
5. Both `/api/v1` and `/api/v2` remain live simultaneously

---

## Deprecation Policy

- v1 stays live for **6 months** after v2 launches
- Add a `Sunset` header to v1 responses during the deprecation window:
  ```python
  response.headers["Sunset"] = "Sat, 01 Jan 2028 00:00:00 GMT"
  response.headers["Deprecation"] = "true"
  ```
- Communicate the deprecation date in release notes and API docs

---

## Client Migration

The frontend reads `API_BASE` from environment:
```
NEXT_PUBLIC_API_URL=https://api.meltingmoments.ae/api/v1
```

To migrate a client to v2, update `API_BASE` to point at `/api/v2`. Endpoints that haven't changed are safe to call on v1 indefinitely during the deprecation window.

---

## Versioning Scope

Versioning applies to the **public API only** (routes under `/api/v*`). Internal admin endpoints follow the same convention but may be updated more aggressively since they are not consumed by third parties.
