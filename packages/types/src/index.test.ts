import { describe, it, expect, expectTypeOf } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { AddressCreate, Order, OrderStatus } from "./index";

/**
 * These pin the exact drifts the hand-written era shipped. Each one was a real
 * mismatch found in the 2026-08 architecture audit; if a regeneration ever
 * loses them again, the contract moved and somebody should look at why.
 *
 * They assert against `openapi.json` rather than against the TypeScript types,
 * because vitest does not typecheck: a `Record<OrderStatus, true>` here would
 * compile-fail in an editor and pass in CI, which is the same false assurance
 * this file exists to remove. Reading the document makes the check real.
 *
 * Scope worth knowing: these guard the *generated* contract. An app holding a
 * hand-written copy can still drift without failing here — which is what
 * happened up to 2026-08-23, when admin carried eight of the eleven statuses
 * and rendered `undefined` for the rest. Until the apps import these types,
 * widening an enum still means grepping each app's own `lib/types.ts`.
 */

const openapi = JSON.parse(
  readFileSync(fileURLToPath(new URL("../openapi.json", import.meta.url)), "utf8"),
) as {
  components: {
    schemas: Record<string, { enum?: string[]; required?: string[] }>;
  };
};

const schema = (name: string) => {
  const found = openapi.components.schemas[name];
  if (!found) throw new Error(`${name} is not in openapi.json`);
  return found;
};

describe("generated contract", () => {
  it("OrderStatus carries every API value, exhaustively", () => {
    // Exhaustive against the document, so adding a status to the API fails
    // here until somebody has looked at every screen that switches on it.
    // The old version asserted a hand-written 10-item array had length 10 —
    // true of any array, and blind to `arrived_at_pos` when it was added.
    expect(schema("OrderStatusEnum").enum).toEqual([
      "created",
      "confirmed",
      "arrived_at_pos",
      "packed",
      "out_for_delivery",
      "delivered",
      "undelivered",
      "cancelled",
      "payment_failed",
      "refunded",
      "disputed",
    ]);
  });

  it("an order exposes the fields the admin's copy was missing", () => {
    const props = Object.keys(
      (schema("OrderResponse") as unknown as { properties: object }).properties,
    );
    expect(props).toEqual(
      expect.arrayContaining(["low_order_fee", "email_has_account", "locale"]),
    );
    expectTypeOf<Order>().toHaveProperty("low_order_fee");
    expectTypeOf<Order>().toHaveProperty("email_has_account");
    expectTypeOf<Order>().toHaveProperty("locale");
  });

  it("an address pin is required, as zone pricing demands", () => {
    // The web app's hand-written copy made these optional, inviting 422s (or
    // a "fix" that would have broken zone pricing). Pydantic requires them.
    expect(schema("AddressCreate").required).toEqual(
      expect.arrayContaining(["latitude", "longitude"]),
    );
    expectTypeOf<AddressCreate["latitude"]>().not.toEqualTypeOf<undefined>();
    expectTypeOf<AddressCreate["longitude"]>().not.toEqualTypeOf<undefined>();
  });
});
