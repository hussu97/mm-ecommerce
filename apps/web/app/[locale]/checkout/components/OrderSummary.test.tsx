/**
 * The checkout summary renders; it does not calculate.
 *
 * These used to be tests of `lowOrderFeeFor` — a TypeScript mirror of the
 * server's `low_order_fee_for`, threshold rule and all — plus a component that
 * added the fee into its own grand total and printed VAT from
 * `(subtotal - discount) * 5 / 105`. Both are gone. Every figure now arrives on
 * `POST /orders/preview`, priced by the same code that writes the order, so
 * what is left to check here is that the right numbers land in the right lines
 * and that the copy around them still says the right thing.
 *
 * The fee rule itself is tested where it lives: `apps/api`'s
 * `test_low_order_fee.py`, and `test_preview_matches_creation.py` for the
 * property that the preview and the order agree.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { OrderSummary } from './OrderSummary';
import type { Cart, DeliveryQuote, OrderPreview } from '@/lib/types';

// The threshold and amount the explanation quotes. They reach the page over
// `/delivery/rates` — deliberately not imported from anywhere in the app, so a
// change to the settings row cannot silently make these tests describe a fee
// nobody is charged.
const LOW_ORDER_FEE = 15;
const LOW_ORDER_THRESHOLD = 35;

// The real strings, so what these assertions read is what a customer reads.
const STRINGS: Record<string, string> = {
  'common.subtotal': 'Subtotal',
  'common.discount': 'Discount',
  'common.delivery': 'Delivery',
  'common.free': 'Free',
  'common.total': 'Total',
  'checkout.store_pickup': 'Store pickup',
  'checkout.low_order_fee': 'Small order fee',
  'checkout.low_order_fee_remaining': 'Add {amount} AED more to remove this fee',
  'checkout.low_order_fee_what_is_this': 'What is this?',
  'checkout.low_order_fee_info':
    'Orders of {threshold} AED or less carry a {fee} AED fee. It covers the cost of '
    + 'baking, boxing and getting a small order to you — add {remaining} AED more and '
    + 'it comes off.',
  'checkout.fee_from_address': 'Calculated from your address',
  'checkout.unserviceable_short': "We don't deliver here",
};

const t = (key: string, params?: Record<string, string | number>) => {
  let value = STRINGS[key] ?? key;
  for (const [k, v] of Object.entries(params ?? {})) value = value.replace(`{${k}}`, String(v));
  return value;
};

function cartOf(subtotal: number): Cart {
  return {
    id: 'cart-1',
    items: [
      {
        id: 'item-1',
        product_id: 'p1',
        product_name: 'Chocolate cake',
        quantity: 1,
        unit_price: subtotal,
        line_total: subtotal,
      },
    ],
    subtotal,
    item_count: 1,
  } as unknown as Cart;
}

function quoteOf(overrides: Partial<DeliveryQuote> = {}): DeliveryQuote {
  return {
    delivery_fee: 20,
    base_fee: 20,
    free_delivery_applied: false,
    free_delivery_available: true,
    free_threshold: 150,
    remaining_for_free: 0,
    zone_name: 'Dubai',
    in_known_zone: true,
    delivery_estimate: null,
    serviceable: true,
    ...overrides,
  };
}

/**
 * A preview as the server would send it. Written out rather than computed,
 * because a test that derives these from each other would be reintroducing
 * exactly the client-side arithmetic this change deleted.
 */
function previewOf(overrides: Partial<OrderPreview> = {}): OrderPreview {
  return {
    subtotal: 20,
    discount_amount: 0,
    delivery_fee: 20,
    low_order_fee: LOW_ORDER_FEE,
    vat_rate: 0.05,
    vat_amount: 0.95,
    total_excl_vat: 19.05,
    total: 55,
    promo: null,
    delivery: quoteOf(),
    unavailable_items: [],
    ...overrides,
  };
}

function renderSummary(
  subtotal: number,
  preview: OrderPreview | null,
  overrides: Record<string, unknown> = {},
) {
  return render(
    <OrderSummary
      cart={cartOf(subtotal)}
      retryOrder={null}
      preview={preview}
      lowOrderThreshold={LOW_ORDER_THRESHOLD}
      lowOrderFeeAmount={LOW_ORDER_FEE}
      hasPin
      deliveryMethod="delivery"
      unserviceable={false}
      locale="en"
      t={t}
      {...overrides}
    />,
  );
}

describe('OrderSummary — the small-basket fee', () => {
  it('charges a small delivery, and says what it takes to be rid of it', () => {
    renderSummary(20, previewOf());

    expect(screen.getByText('Small order fee')).toBeInTheDocument();
    expect(screen.getByText(`${LOW_ORDER_FEE.toFixed(2)} AED`)).toBeInTheDocument();
    // 15.01, not 15.00 — the threshold is inclusive, so spending exactly the
    // round number would leave the fee exactly where it was.
    expect(screen.getByText('Add 15.01 AED more to remove this fee')).toBeInTheDocument();
  });

  it('disappears entirely above the threshold, unlike the delivery line', () => {
    renderSummary(120, previewOf({ subtotal: 120, low_order_fee: 0, total: 140 }));

    expect(screen.queryByText('Small order fee')).toBeNull();
    // The contrast is deliberate: delivery is a charge worth showing at zero
    // because a waived one is a saving. A surcharge printed at 0.00 is just the
    // shop mentioning it charges small orders to someone who did not place one.
    expect(screen.getByText('Delivery')).toBeInTheDocument();
  });

  it('is not a line on a collection order', () => {
    // The server does not charge one either — a box handed over a counter costs
    // us nothing extra — so a pickup preview simply comes back with zero.
    renderSummary(20, previewOf({ delivery_fee: 0, low_order_fee: 0, total: 20 }), {
      deliveryMethod: 'pickup',
    });

    expect(screen.queryByText('Small order fee')).toBeNull();
  });

  // Hover, keyboard and dismissal are `InfoTip`'s own tests; this only checks
  // that the fee line hangs the right explanation off it.
  it('explains itself on demand', () => {
    renderSummary(20, previewOf());

    const trigger = screen.getByRole('button', { name: 'What is this?' });
    expect(screen.queryByRole('tooltip')).toBeNull();

    fireEvent.click(trigger);

    expect(screen.getByRole('tooltip')).toHaveTextContent(
      `Orders of ${LOW_ORDER_THRESHOLD} AED or less carry a ${LOW_ORDER_FEE} AED fee`,
    );
  });
});

describe('OrderSummary — it prints what the server priced', () => {
  it('shows the total the preview named, not one of its own', () => {
    // Deliberately not 20 + 20 + 15. The component must not be able to arrive
    // at the right answer by adding the lines up — that is the calculation this
    // change removed, and a test that only ever passes it a consistent preview
    // would not notice its return.
    renderSummary(20, previewOf({ total: 41.5 }));

    expect(screen.getByText('41.50 AED')).toBeInTheDocument();
  });

  it('prints the VAT the server declared, which is not 5/105 of the total', () => {
    // The old line was `(subtotal - discount) * 5 / 105`, computed here and
    // ignoring both fees — so the figure under "Total" was never 5/105 of it.
    // VAT is on the goods alone, and the server says how much.
    renderSummary(20, previewOf({ vat_amount: 0.95, vat_rate: 0.05 }));

    expect(screen.getByText(/VAT included \(5%\) · 0\.95 AED/)).toBeInTheDocument();
  });

  it('names the discount and the code the server accepted', () => {
    renderSummary(40, previewOf({
      subtotal: 40,
      discount_amount: 6,
      low_order_fee: 0,
      total: 54,
      promo: {
        code: 'FIRST15',
        valid: true,
        message: null,
        discount_amount: 6,
        requires_phone_verification: false,
        phone_verified: true,
      },
    }));

    expect(screen.getByText('Discount (FIRST15)')).toBeInTheDocument();
    expect(screen.getByText('-6.00 AED')).toBeInTheDocument();
    // Judged on the gross 40 by the server, so no surcharge follows the coupon
    // in. The rule lives on the API; this only checks the line is absent when
    // the server says it is zero.
    expect(screen.queryByText('Small order fee')).toBeNull();
  });

  it('shows the basket alone before the first preview lands', () => {
    // One round trip, and exactly what this screen has always shown on its
    // first paint: no fee, no surcharge, no discount, because none of them are
    // known yet.
    renderSummary(20, null);

    expect(screen.getByText('Calculated from your address')).toBeInTheDocument();
    expect(screen.getAllByText('20.00 AED').length).toBeGreaterThan(0);
  });

  it('strikes the fee through when the server says it is waived', () => {
    // The saving is the point of the line. Printing "Free" alone tells the
    // customer nothing about what they no longer owe — and whether the offer
    // applies is the server's call, not a comparison this component can make:
    // free delivery reaches only the zones we price ourselves.
    renderSummary(200, previewOf({
      subtotal: 200,
      delivery_fee: 0,
      low_order_fee: 0,
      total: 200,
      delivery: quoteOf({ delivery_fee: 0, base_fee: 25, free_delivery_applied: true }),
    }));

    expect(screen.getByText('25.00 AED')).toBeInTheDocument();
    expect(screen.getByText('Free')).toBeInTheDocument();
  });

  it('refuses to name a total for an address nothing can be delivered to', () => {
    renderSummary(
      20,
      previewOf({ delivery_fee: null, total: 35, delivery: quoteOf({ serviceable: false }) }),
      { unserviceable: true },
    );

    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('35.00 AED')).toBeNull();
  });
});
