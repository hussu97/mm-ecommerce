/**
 * Shared TypeScript types — generated from the API, not transcribed from it.
 *
 * This package used to be thirty lines of hand-written guesses, and the
 * guesses were wrong: `OrderStatus` had four of the API's ten values,
 * `PaymentProvider` named gateways the shop had never shipped, and nothing
 * imported any of it — which made it a trap rather than a source. An agent (or
 * an engineer) told "shared types live in @mm/types" would faithfully copy a
 * contract months out of date.
 *
 * Now the flow is mechanical:
 *
 *   apps/api schemas ──(python -m scripts.export_openapi)──▶ openapi.json
 *   openapi.json ──(pnpm --filter @mm/types generate)──▶ src/generated.ts
 *
 * CI checks both hops for freshness, so a Pydantic change that skips this
 * package fails the build instead of shipping as drift. `generated.ts` is the
 * whole contract (`paths`, `components`, `operations`); this file curates the
 * names the apps actually reach for.
 */

import type { components, operations, paths } from "./generated";

export type { components, operations, paths };

/** Every response/request schema, by its Pydantic name. */
export type Schemas = components["schemas"];

// ── the enums that used to drift ──────────────────────────────────────────────

export type OrderStatus = Schemas["OrderStatusEnum"];
export type DeliveryMethod = Schemas["DeliveryMethodEnum"];

// ── the core entities ─────────────────────────────────────────────────────────

export type Order = Schemas["OrderResponse"];
export type OrderListItem = Schemas["OrderListResponse"];
export type Product = Schemas["ProductResponse"];
export type Category = Schemas["CategoryResponse"];
export type Cart = Schemas["CartResponse"];
export type Address = Schemas["AddressResponse"];
export type AddressCreate = Schemas["AddressCreate"];
export type PromoCode = Schemas["PromoCodeResponse"];
export type Fulfilment = Schemas["FulfilmentResponse"];
