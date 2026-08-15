import { describe, expect, it } from 'vitest';

import {
  checkoutGateBehaviour,
  checkoutGateLabel,
  isValidEmail,
  resolveCheckoutGate,
  type CheckoutGate,
  type CheckoutGateInput,
} from './checkout-gate';

/**
 * A checkout with nothing left to do: delivery, pinned, serviceable, named,
 * phoned, emailed, priced. Every test below breaks exactly one thing about it
 * and asserts which state that produces — which is also how the priority order
 * is proved, by breaking two and watching the earlier one win.
 */
const READY: CheckoutGateInput = {
  submitting: false,
  restoring: false,
  hasUnpaidOrder: false,
  deliveryMethod: 'delivery',
  branchesLoaded: true,
  pickupBranchId: '',
  addressLine1: 'Villa 12, Al Barsha',
  hasPin: true,
  unserviceable: false,
  unavailableCount: 0,
  firstName: 'Fatema',
  phone: '+971501234567',
  phoneValid: true,
  verificationOutstanding: false,
  accountEmail: null,
  email: 'fatema@example.ae',
  pricing: false,
};

/** The same, collecting from a store that has been chosen. */
const READY_PICKUP: CheckoutGateInput = {
  ...READY,
  deliveryMethod: 'pickup',
  pickupBranchId: 'branch-jumeirah',
  addressLine1: '',
  hasPin: false,
};

const gate = (patch: Partial<CheckoutGateInput> = {}, base = READY): CheckoutGate['kind'] =>
  resolveCheckoutGate({ ...base, ...patch }).kind;

describe('resolveCheckoutGate — every state', () => {
  it('ready when the form is complete and priced', () => {
    expect(gate()).toBe('ready');
    expect(gate({}, READY_PICKUP)).toBe('ready');
  });

  it('submitting while the order is being written', () => {
    expect(gate({ submitting: true })).toBe('submitting');
  });

  it('restoring while a returned unpaid order is being looked up', () => {
    expect(gate({ restoring: true })).toBe('restoring');
  });

  it('pay_now when a returned unpaid order was found', () => {
    expect(gate({ hasUnpaidOrder: true })).toBe('pay_now');
  });

  it('pickup_branch when collecting and no store is chosen', () => {
    expect(gate({ pickupBranchId: '' }, READY_PICKUP)).toBe('pickup_branch');
  });

  it('address when the line is blank', () => {
    expect(gate({ addressLine1: '   ' })).toBe('address');
  });

  it('address when there is a line but no map pin', () => {
    // The gap this gate was written for: the API requires the coordinates and
    // nothing on this screen used to check for them.
    expect(gate({ hasPin: false })).toBe('address');
  });

  it('unserviceable when the pin is outside every zone', () => {
    expect(gate({ unserviceable: true })).toBe('unserviceable');
  });

  it('items_unavailable when the branch serving this pin has run out', () => {
    expect(gate({ unavailableCount: 1 })).toBe('items_unavailable');
  });

  it('asks for the address before naming what that address cannot get', () => {
    // The branch comes from the pin, so a basket judged against no address is
    // judging against no branch. Complaining about stock before there is a shop
    // to be out of it would be a sentence nobody can act on.
    expect(gate({ unavailableCount: 1, hasPin: false })).toBe('address');
    expect(gate({ unavailableCount: 1, unserviceable: true })).toBe('unserviceable');
  });

  it('stops on unavailable items before asking for anything merely unfinished', () => {
    // A basket that cannot be made is not the customer's omission, and letting
    // them type a phone number and an email first only to refuse at the button
    // is the shape this replaces.
    expect(gate({ unavailableCount: 1, email: '' })).toBe('items_unavailable');
    expect(gate({ unavailableCount: 1, verificationOutstanding: true })).toBe(
      'items_unavailable',
    );
    expect(gate({ unavailableCount: 1, pricing: true })).toBe('items_unavailable');
  });

  it('collection is judged on stock too', () => {
    // A chosen store can be out of something just as a delivery branch can, and
    // the customer is about to drive to it.
    expect(gate({ unavailableCount: 1 }, READY_PICKUP)).toBe('items_unavailable');
  });

  it('an unpaid order is past the point of editing the basket', () => {
    // Its lines are already written and already claimed; what it needs is
    // paying for. Offering to remove something would change nothing it owes.
    expect(gate({ unavailableCount: 1, hasUnpaidOrder: true })).toBe('pay_now');
  });

  it('address_contact when the address has no name or no usable phone', () => {
    expect(gate({ firstName: '  ' })).toBe('address_contact');
    expect(gate({ phone: '' })).toBe('address_contact');
    expect(gate({ phoneValid: false })).toBe('address_contact');
  });

  it('verify_phone when a coupon still owes a proved number', () => {
    expect(gate({ verificationOutstanding: true })).toBe('verify_phone');
  });

  it('contact when collecting and the name or phone is missing', () => {
    expect(gate({ firstName: '' }, READY_PICKUP)).toBe('contact');
    expect(gate({ phone: '   ' }, READY_PICKUP)).toBe('contact');
    expect(gate({ phoneValid: false }, READY_PICKUP)).toBe('contact');
  });

  it('email when it is blank or malformed', () => {
    expect(gate({ email: '' })).toBe('email');
    expect(gate({ email: '   ' })).toBe('email');
    expect(gate({ email: 'fatema@localhost' })).toBe('email');
    expect(gate({ email: 'not-an-email' })).toBe('email');
  });

  it('pricing while the first server total is still in flight', () => {
    expect(gate({ pricing: true })).toBe('pricing');
  });
});

describe('resolveCheckoutGate — the exemptions', () => {
  it('never asks a signed-in customer for an email', () => {
    // It came from the account, not the keyboard, and there is no field on the
    // form to fix it in.
    expect(gate({ accountEmail: 'fatema@meltingmoments.ae', email: '' })).toBe('ready');
    expect(gate({ accountEmail: 'fatema@meltingmoments.ae', email: 'rubbish' })).toBe('ready');
  });

  it('does not demand a store when the branch list never loaded', () => {
    // A failed GET must not strand the order: the API resolves a branch itself.
    expect(gate({ branchesLoaded: false, pickupBranchId: '' }, READY_PICKUP)).toBe('ready');
  });

  it('ignores delivery-only requirements when collecting', () => {
    expect(gate({ addressLine1: '', hasPin: false, unserviceable: true }, READY_PICKUP)).toBe('ready');
  });

  it('ignores the pickup branch when delivering', () => {
    expect(gate({ pickupBranchId: '' })).toBe('ready');
  });
});

describe('resolveCheckoutGate — priority order', () => {
  /**
   * Everything wrong at once. Walking this list from the top and repairing one
   * requirement at a time must produce the states in exactly the documented
   * order — which is the property the whole design rests on, because it is what
   * makes the button walk down the page rather than jump around it.
   */
  it('walks a delivery down the form in visual order', () => {
    const broken: CheckoutGateInput = {
      ...READY,
      submitting: true,
      restoring: true,
      hasUnpaidOrder: true,
      addressLine1: '',
      hasPin: false,
      unserviceable: true,
      firstName: '',
      phone: '',
      phoneValid: false,
      verificationOutstanding: true,
      email: '',
      pricing: true,
    };

    expect(gate({}, broken)).toBe('submitting');
    expect(gate({ submitting: false }, broken)).toBe('restoring');
    expect(gate({ submitting: false, restoring: false }, broken)).toBe('pay_now');

    const form = { ...broken, submitting: false, restoring: false, hasUnpaidOrder: false };
    expect(gate({}, form)).toBe('address');
    expect(gate({ addressLine1: 'Villa 12', hasPin: true }, form)).toBe('unserviceable');
    expect(
      gate({ addressLine1: 'Villa 12', hasPin: true, unserviceable: false }, form),
    ).toBe('address_contact');
    expect(
      gate(
        {
          addressLine1: 'Villa 12', hasPin: true, unserviceable: false, unavailableCount: 0,
          firstName: 'Fatema', phone: '+971501234567', phoneValid: true,
        },
        form,
      ),
    ).toBe('verify_phone');
    expect(
      gate(
        {
          addressLine1: 'Villa 12', hasPin: true, unserviceable: false, unavailableCount: 0,
          firstName: 'Fatema', phone: '+971501234567', phoneValid: true,
          verificationOutstanding: false,
        },
        form,
      ),
    ).toBe('email');
    expect(
      gate(
        {
          addressLine1: 'Villa 12', hasPin: true, unserviceable: false, unavailableCount: 0,
          firstName: 'Fatema', phone: '+971501234567', phoneValid: true,
          verificationOutstanding: false, email: 'fatema@example.ae',
        },
        form,
      ),
    ).toBe('pricing');
    expect(
      gate(
        {
          addressLine1: 'Villa 12', hasPin: true, unserviceable: false, unavailableCount: 0,
          firstName: 'Fatema', phone: '+971501234567', phoneValid: true,
          verificationOutstanding: false, email: 'fatema@example.ae',
          pricing: false,
        },
        form,
      ),
    ).toBe('ready');
  });

  it('walks a collection down the form in visual order', () => {
    const form: CheckoutGateInput = {
      ...READY_PICKUP,
      pickupBranchId: '',
      firstName: '',
      phone: '',
      phoneValid: false,
      email: '',
      pricing: true,
    };

    expect(gate({}, form)).toBe('pickup_branch');
    expect(gate({ pickupBranchId: 'branch-jumeirah' }, form)).toBe('contact');
    expect(
      gate(
        { pickupBranchId: 'branch-jumeirah', firstName: 'Fatema', phone: '+971501234567', phoneValid: true },
        form,
      ),
    ).toBe('email');
    expect(
      gate(
        {
          pickupBranchId: 'branch-jumeirah', firstName: 'Fatema', phone: '+971501234567',
          phoneValid: true, email: 'fatema@example.ae',
        },
        form,
      ),
    ).toBe('pricing');
  });

  it('a returned unpaid order skips every form requirement', () => {
    // Its address, its coupon and its total are already written; there is
    // nothing on this screen that could change them.
    expect(
      gate({
        hasUnpaidOrder: true,
        addressLine1: '',
        hasPin: false,
        unserviceable: true,
        firstName: '',
        phone: '',
        phoneValid: false,
        verificationOutstanding: true,
        email: '',
        pricing: true,
      }),
    ).toBe('pay_now');
  });

  it('the phone gate outranks the email and the price', () => {
    expect(gate({ verificationOutstanding: true, email: '', pricing: true })).toBe('verify_phone');
  });

  it('the email outranks the price', () => {
    expect(gate({ email: '', pricing: true })).toBe('email');
  });
});

describe('checkoutGateBehaviour', () => {
  const kinds: CheckoutGate['kind'][] = [
    'submitting', 'restoring', 'pickup_branch', 'address', 'unserviceable',
    'address_contact', 'verify_phone', 'contact', 'email', 'pricing',
    'pay_now', 'ready',
  ];

  it('describes every state', () => {
    for (const kind of kinds) {
      const behaviour = checkoutGateBehaviour({ kind } as CheckoutGate);
      expect(behaviour.labelKey).toBeTruthy();
      expect(behaviour.labelFallback).toBeTruthy();
    }
  });

  it('disables only the three states a press cannot help', () => {
    const disabled = kinds.filter((kind) => checkoutGateBehaviour({ kind } as CheckoutGate).disabled);
    expect(disabled).toEqual(['submitting', 'restoring', 'pricing']);
  });

  it('sends the four modal-resolved states to the address modal', () => {
    const opensModal = kinds.filter(
      (kind) => checkoutGateBehaviour({ kind } as CheckoutGate).action.kind === 'address',
    );
    expect(opensModal).toEqual(['address', 'unserviceable', 'address_contact', 'verify_phone']);
  });

  it('reveals the two sections that are edited on the page itself', () => {
    expect(checkoutGateBehaviour({ kind: 'pickup_branch' }).action)
      .toEqual({ kind: 'reveal', field: 'pickupBranch' });
    expect(checkoutGateBehaviour({ kind: 'contact' }).action)
      .toEqual({ kind: 'reveal', field: 'contact' });
    expect(checkoutGateBehaviour({ kind: 'email' }).action)
      .toEqual({ kind: 'reveal', field: 'email' });
  });

  it('submits only when there is something to submit', () => {
    const submits = kinds.filter(
      (kind) =>
        checkoutGateBehaviour({ kind } as CheckoutGate).action.kind === 'submit' &&
        !checkoutGateBehaviour({ kind } as CheckoutGate).disabled,
    );
    expect(submits).toEqual(['pay_now', 'ready']);
  });

  it('names a reportable field for every deflecting state and none of the rest', () => {
    const reported = kinds.filter((kind) => checkoutGateBehaviour({ kind } as CheckoutGate).errorField);
    expect(reported).toEqual([
      'pickup_branch', 'address', 'unserviceable', 'address_contact',
      'verify_phone', 'contact', 'email',
    ]);
  });
});

describe('checkoutGateLabel', () => {
  /** A translation table that has none of these keys yet. */
  const missing = (key: string) => key;
  /** One that has them all, and interpolates. */
  const seeded = (key: string, params?: Record<string, string | number>) =>
    key === 'checkout.place_order' ? `تأكيد الطلب · ${params?.total} درهم` : `«${key}»`;

  it('quotes the total on the ready button', () => {
    expect(checkoutGateLabel({ kind: 'ready' }, seeded, 120.5)).toBe('تأكيد الطلب · 120.50 درهم');
  });

  it('falls back to English when a key is not seeded yet', () => {
    // `checkout.estimated_delivery` once shipped to customers verbatim; these
    // keys land in a later deploy than the code that reads them.
    expect(checkoutGateLabel({ kind: 'email' }, missing, 0)).toBe('Enter email address');
    expect(checkoutGateLabel({ kind: 'ready' }, missing, 99)).toBe('Place Order · 99.00 AED');
  });

  it('uses the translation when there is one', () => {
    expect(checkoutGateLabel({ kind: 'verify_phone' }, seeded, 0)).toBe('«checkout.verify_your_phone»');
  });

  it('quotes the total on the pay-now button too', () => {
    /**
     * `checkout.pay_now` was seeded as "Pay Now — {total} AED" for the
     * returned-unpaid-order flow years before this gate existed. Sending it
     * down the non-interpolating path printed the placeholder itself onto the
     * button, on the one screen that asks for money.
     */
    const withPayNow = (key: string, params?: Record<string, string | number>) =>
      key === 'checkout.pay_now' ? `Pay Now — ${params?.total} AED` : `«${key}»`;

    expect(checkoutGateLabel({ kind: 'pay_now' }, withPayNow, 54)).toBe('Pay Now — 54.00 AED');
    expect(checkoutGateLabel({ kind: 'pay_now' }, withPayNow, 54)).not.toContain('{total}');
    // And the English fallback names the figure as well, for the deploy in
    // which the code is live and the key is not.
    expect(checkoutGateLabel({ kind: 'pay_now' }, missing, 54)).toBe('Pay Now — 54.00 AED');
  });
});

describe('isValidEmail', () => {
  it('accepts an ordinary address', () => {
    expect(isValidEmail('fatema@example.ae')).toBe(true);
  });

  it('rejects blanks and malformed addresses', () => {
    expect(isValidEmail('')).toBe(false);
    expect(isValidEmail('fatema')).toBe(false);
    expect(isValidEmail('fatema@example')).toBe(false);
    expect(isValidEmail('fatema@example.a')).toBe(false);
  });

  it('rejects the hostnames an autofill supplies on a developer machine', () => {
    expect(isValidEmail('fatema@localhost')).toBe(false);
    expect(isValidEmail('fatema@my.local')).toBe(false);
    expect(isValidEmail('fatema@shop.test')).toBe(false);
  });
});
