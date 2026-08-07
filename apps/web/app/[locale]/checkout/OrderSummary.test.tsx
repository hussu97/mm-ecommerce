import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { OrderSummary, lowOrderFeeFor } from './page';

// The real numbers live in `delivery_settings` and reach the page over
// `/delivery/rates`. These stand in for that response — deliberately not
// imported from the page, which no longer holds a copy of them, so a change to
// the settings row cannot silently make these tests describe a fee nobody is
// charged.
const RATES = { low_order_fee: 15, low_order_threshold: 35 };
const LOW_ORDER_FEE = RATES.low_order_fee;
const LOW_ORDER_THRESHOLD = RATES.low_order_threshold as number;
import type { Cart } from '@/lib/types';

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

function renderSummary(subtotal: number, overrides: Record<string, unknown> = {}) {
  const deliveryMethod = (overrides.deliveryMethod as 'delivery' | 'pickup') ?? 'delivery';
  return render(
    <OrderSummary
      cart={cartOf(subtotal)}
      retryOrder={null}
      discount={0}
      promoCode=""
      deliveryFee={20}
      lowOrderFee={lowOrderFeeFor(subtotal, deliveryMethod, RATES)}
      lowOrderThreshold={LOW_ORDER_THRESHOLD}
      lowOrderFeeAmount={LOW_ORDER_FEE}
      baseFee={20}
      freeApplied={false}
      freeAvailable
      hasPin
      remainingForFree={0}
      deliveryMethod={deliveryMethod}
      unserviceable={false}
      locale="en"
      t={t}
      {...overrides}
    />,
  );
}

describe('lowOrderFeeFor', () => {
  it('charges a delivery at or below the threshold', () => {
    expect(lowOrderFeeFor(20, 'delivery', RATES)).toBe(LOW_ORDER_FEE);
    expect(lowOrderFeeFor(LOW_ORDER_THRESHOLD, 'delivery', RATES)).toBe(LOW_ORDER_FEE);
  });

  it('charges nothing once the basket is past it', () => {
    expect(lowOrderFeeFor(LOW_ORDER_THRESHOLD + 0.01, 'delivery', RATES)).toBe(0);
    expect(lowOrderFeeFor(200, 'delivery', RATES)).toBe(0);
  });

  it('never charges a collection, however small', () => {
    expect(lowOrderFeeFor(5, 'pickup', RATES)).toBe(0);
  });

  it('does not price an empty basket', () => {
    expect(lowOrderFeeFor(0, 'delivery', RATES)).toBe(0);
  });
});

describe('OrderSummary — the small-basket fee', () => {
  it('charges a small delivery, and says what it takes to be rid of it', () => {
    renderSummary(20);

    expect(screen.getByText('Small order fee')).toBeInTheDocument();
    expect(screen.getByText(`${LOW_ORDER_FEE.toFixed(2)} AED`)).toBeInTheDocument();
    // 15.01, not 15.00 — the threshold is inclusive, so spending exactly the
    // round number would leave the fee exactly where it was.
    expect(screen.getByText('Add 15.01 AED more to remove this fee')).toBeInTheDocument();
  });

  it('disappears entirely above the threshold, unlike the delivery line', () => {
    renderSummary(120);

    expect(screen.queryByText('Small order fee')).toBeNull();
    // The contrast is deliberate: delivery is a charge worth showing at zero
    // because a waived one is a saving. A surcharge printed at 0.00 is just the
    // shop mentioning it charges small orders to someone who did not place one.
    expect(screen.getByText('Delivery')).toBeInTheDocument();
  });

  it('is not a line on a collection order', () => {
    renderSummary(20, { deliveryMethod: 'pickup', deliveryFee: 0 });

    expect(screen.queryByText('Small order fee')).toBeNull();
  });

  // Hover, keyboard and dismissal are `InfoTip`'s own tests; this only checks
  // that the fee line hangs the right explanation off it.
  it('explains itself on demand', () => {
    renderSummary(20);

    const trigger = screen.getByRole('button', { name: 'What is this?' });
    expect(screen.queryByRole('tooltip')).toBeNull();

    fireEvent.click(trigger);

    expect(screen.getByRole('tooltip')).toHaveTextContent(
      `Orders of ${LOW_ORDER_THRESHOLD} AED or less carry a ${LOW_ORDER_FEE} AED fee`,
    );
  });

  it('is in the total the customer commits to', () => {
    renderSummary(20);

    // 20 basket + 20 delivery + 15 fee.
    expect(screen.getByText('55.00 AED')).toBeInTheDocument();
  });

  it('is judged before the discount, so a coupon cannot conjure it', () => {
    // A 40-dirham basket is above the threshold. A coupon taking it to 34 must
    // not drag a 15-dirham surcharge in behind a 6-dirham saving.
    expect(lowOrderFeeFor(40, 'delivery', RATES)).toBe(0);

    renderSummary(40, { discount: 6, promoCode: 'FIRST15', lowOrderFee: lowOrderFeeFor(40, 'delivery', RATES) });

    expect(screen.queryByText('Small order fee')).toBeNull();
  });
});
