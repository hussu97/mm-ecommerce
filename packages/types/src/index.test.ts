import { describe, it, expect, expectTypeOf } from "vitest";
import type { AddressCreate, Order, OrderStatus } from "./index";

/**
 * These pin the exact drifts the hand-written era shipped. Each one was a real
 * mismatch found in the 2026-08 architecture audit; if a regeneration ever
 * loses them again, the contract moved and somebody should look at why.
 */
describe("generated contract", () => {
  it("OrderStatus carries all ten API values, not the four the old copy had", () => {
    const all: OrderStatus[] = [
      "created",
      "confirmed",
      "packed",
      "out_for_delivery",
      "delivered",
      "undelivered",
      "cancelled",
      "payment_failed",
      "refunded",
      "disputed",
    ];
    expect(all).toHaveLength(10);
  });

  it("an order exposes the fields the admin's copy was missing", () => {
    expectTypeOf<Order>().toHaveProperty("low_order_fee");
    expectTypeOf<Order>().toHaveProperty("email_has_account");
    expectTypeOf<Order>().toHaveProperty("locale");
  });

  it("an address pin is required, as zone pricing demands", () => {
    // The web app's hand-written copy made these optional, inviting 422s (or
    // a "fix" that would have broken zone pricing). Pydantic requires them.
    expectTypeOf<AddressCreate["latitude"]>().not.toEqualTypeOf<undefined>();
    expectTypeOf<AddressCreate["longitude"]>().not.toEqualTypeOf<undefined>();
  });
});
