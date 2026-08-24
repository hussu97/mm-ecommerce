# System shape

What the pieces are and where a request goes. For *how to change* them, read
[`../CLAUDE.md`](../CLAUDE.md); this page only says what is where.

## Four applications, one database

| App | Lives at | Runs on | Talks to the API via |
|---|---|---|---|
| `apps/web` | `meltingmomentscakes.com` | Vercel | `lib/api-client.ts` in the browser, `lib/api-server.ts` in RSC |
| `apps/admin` | `admin.meltingmomentscakes.com` | Vercel | the single `request()` in `lib/api.ts` |
| `apps/api` | `api.meltingmomentscakes.com` | the GCP VM, container `api` | — |
| `apps/api` (register) | `pos.meltingmomentscakes.com` | the same VM, container `pos-api` | — |

`mm-pos`, the iPad register, is a separate Swift repository. It speaks to
`pos-api` and to nothing else.

### Why the API is two containers

`app/main.py` and `app/pos_main.py` are two FastAPI applications over one
codebase and one database, with **different route tables**: 64 routers on the
storefront/admin API, 26 on the register. `app/pos_main.py` says why in its own
docstring, and it is worth knowing before adding a route:

- A terminal on a shop counter should not be able to reach customer accounts,
  the CMS, or the bulk import endpoints. Narrowing the surface beats
  per-endpoint checking.
- Tills keep trading while the website is deployed, restarted, or hammered by a
  campaign, because they are no longer the same process.

A third hostname, `aggregator-api`, is nginx routing for marketplace webhooks;
it is not a third application.

## Where a request goes

```
browser / register
      │
      ▼
   nginx  ── by hostname ──▶  api  |  pos-api
      │
      ▼
  app/api/v1/<resource>.py     routers: auth, permissions via require(),
      │                        schema mapping. No state changes here.
      ▼
  app/services/<domain>.py     business logic. Services flush(); the
      │                        request-scoped get_db dependency commits.
      ▼
  app/models/<table>.py        SQLAlchemy. Money is Numeric, timestamps are
                               tz-aware, statuses are String + CHECK.
```

Two things sit beside that path rather than in it:

- `app/services/providers/` — the HTTP clients for Lalamove, noon Send,
  Slider, Stripe, Ziina, Tabby, Tamara, GrubOps, Mapbox and APNs. A `provider`
  speaks somebody else's protocol; the `service` beside it decides when to.
- `app/core/` — config, database session, exceptions, permissions, money,
  images, phone. `Settings` is the only reader of the environment.

## The parts worth knowing before you touch them

**Order state.** One function assigns `Order.status`:
`app/services/orders/order_lifecycle.transition()`. It validates against
`VALID_TRANSITIONS` *and* carries the consequences — refund, restock, register
void, publish, dispatch. An AST test fails on any direct assignment. Three
sibling columns (`pos_status`, `delivery_status`, `courier_status`) describe
different things and are not synchronised by it.

**Couriers.** Three of them — Lalamove, noon Send, Slider — with one shape
each: quote, dispatch, cancel, webhook, advance the order. `batching_service`
groups deliveries into windows and dispatches a batch when its window closes;
`fulfilment_reassignment` moves an order between couriers when one refuses.

**Money.** Computed server-side, always. One module quantises it:
`app/core/money.py`. A client-side formula mirroring a server one is a bug.

**i18n.** Every UI string is owned by `apps/api/scripts/seed_i18n.py`, which
the API runs on boot and which overwrites the table. A migration cannot change
a UI string — see canon rule 7.

**Contracts.** `packages/types` is generated from the API's OpenAPI document
and CI fails on drift. Adoption is unfinished: both apps declare it, neither
imports it yet, and ~2,650 lines of hand-written types still shadow it.

## Data

102 tables. [`schema.md`](schema.md) draws the thirteen storefront ones; for
anything else read the model, which carries the reasoning a diagram cannot.
The Alembic chain is linear — one root, one head, no branches — and
`test_migration_chain.py` asserts that on every PR.
