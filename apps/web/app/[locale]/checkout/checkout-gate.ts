import { withFallback } from '@/lib/i18n/fallback';

/**
 * What the place-order button is *for*, right now.
 *
 * The button used to have two faces — "Place Order · 120.00 AED" and, when the
 * pin was outside every zone, "Delivery unavailable here" — and pressing it in
 * any other unfinished state ran the whole validation pass, painted three
 * fields red at once and scrolled to the first of them. That is a form telling
 * a customer everything it dislikes about them in one go, at the moment they
 * were trying to pay.
 *
 * So the button names the *next single thing* instead. The state is resolved
 * from the form in visual order, first unmet wins, which means the label walks
 * the customer down the page in the order the page is read: choose a store,
 * set an address, complete its details, prove the number, enter contact
 * details, enter an email — and only then quote a total. Pressing it does
 * whatever that label promises rather than validating.
 *
 * This is the guide. `handleSubmit` keeps its own validation pass as the
 * guarantee: the gate reads the form the browser has, the API judges the order
 * the server writes, and the two are allowed to disagree — a coupon that
 * expired between the last keystroke and the press, a basket edited in another
 * tab. When they do, the server wins and the validation catches it.
 *
 * Pure, and separate from the page, so every one of these twelve states and
 * the priority between them can be asserted in a test rather than reasoned
 * about inside a thousand-line component. See `checkout-gate.test.ts`.
 */
export type CheckoutGate =
  | { kind: 'submitting' }
  | { kind: 'restoring' }
  | { kind: 'pickup_branch' }
  | { kind: 'address' }
  | { kind: 'unserviceable' }
  | { kind: 'address_contact' }
  | { kind: 'verify_phone' }
  | { kind: 'contact' }
  | { kind: 'email' }
  | { kind: 'pricing' }
  | { kind: 'pay_now' }
  | { kind: 'ready' };

export interface CheckoutGateInput {
  /** The order is being written and paid for. Nothing else matters. */
  submitting: boolean;
  /** A returned unpaid order is still being looked up — see `useRetryOrder`. */
  restoring: boolean;
  /**
   * A returned unpaid order was found. Its figures are written, its address is
   * written, its coupon was judged when it was created; there is no form left
   * to check and the only thing the button can do is open the gateway again.
   */
  hasUnpaidOrder: boolean;
  deliveryMethod: 'delivery' | 'pickup';
  /**
   * Whether the branch list actually arrived. A failed GET must not strand a
   * pickup order behind "Choose a store" with nothing to choose from — the API
   * resolves a branch itself in that case, which is the better trade than
   * losing the sale.
   */
  branchesLoaded: boolean;
  pickupBranchId: string;
  addressLine1: string;
  /**
   * Whether a map pin was actually dropped.
   *
   * A real gap until now: the API requires latitude and longitude on a delivery
   * address and nothing on this screen ever checked for them, so an address
   * typed without touching the map reached `POST /orders` and was refused there
   * — after the customer had finished the form.
   */
  hasPin: boolean;
  unserviceable: boolean;
  firstName: string;
  phone: string;
  /** `isValidPhone(phone)`. Passed in so this module owes nothing to libphonenumber. */
  phoneValid: boolean;
  verificationOutstanding: boolean;
  /**
   * The signed-in account's address, when there is one. Present means the email
   * is not a field on this form at all and is never re-checked — it came from
   * the account, not the keyboard.
   */
  accountEmail: string | null;
  email: string;
  /**
   * The first server-priced total has not landed and one is still on its way.
   * A *stale* total is not this state: showing figures that are one edit old
   * beats a button that flickers to "Calculating total…" on every keystroke.
   */
  pricing: boolean;
}

/**
 * Good enough to receive a receipt.
 *
 * Lives here rather than in the page because the gate is what acts on the
 * answer, and `handleSubmit`'s backstop has to ask the same question the button
 * was labelled from — two spellings of "valid email" is how a button that says
 * "Place order" refuses the order it offered.
 *
 * Deliberately not RFC 5322: the useful failures are a missing dot in the
 * domain and the local-only hostnames a developer's autofill supplies, not the
 * exotic addresses the specification permits.
 */
export function isValidEmail(email: string): boolean {
  if (!email) return false;
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) return false;
  const domain = email.split('@')[1]?.toLowerCase() ?? '';
  if (/\.(local|localhost|example|test|invalid|internal)$/.test(domain)) return false;
  if (domain === 'localhost') return false;
  return true;
}

/**
 * The first unmet requirement, in the order the page is read.
 *
 * The sequence is the whole design. `submitting` and `restoring` come first
 * because they describe the button rather than the form. A returned unpaid
 * order short-circuits everything below it — that order exists and its details
 * are no longer editable, so asking for an email it already carries would be
 * asking for something that could not change the outcome. After that it is the
 * page, top to bottom, and `pricing` sits at the bottom because it is the only
 * one the customer cannot act on.
 */
export function resolveCheckoutGate(input: CheckoutGateInput): CheckoutGate {
  if (input.submitting) return { kind: 'submitting' };
  if (input.restoring) return { kind: 'restoring' };
  if (input.hasUnpaidOrder) return { kind: 'pay_now' };

  const isDelivery = input.deliveryMethod === 'delivery';

  if (!isDelivery) {
    if (input.branchesLoaded && !input.pickupBranchId) return { kind: 'pickup_branch' };
  } else {
    // No line and no pin are one state on purpose: both are fixed in the same
    // modal, by the same gesture, and "Set address" is the honest label for
    // either. Splitting them would mean a second label for a customer who
    // cannot tell which half they are missing.
    if (!input.addressLine1.trim() || !input.hasPin) return { kind: 'address' };
    if (input.unserviceable) return { kind: 'unserviceable' };
    // Name and phone for a delivery live *inside* the address modal, so they
    // are a different state from the pickup form's contact fields even though
    // they are the same three values — the fix is "open the modal", not
    // "scroll down".
    if (!input.firstName.trim() || !input.phone.trim() || !input.phoneValid) {
      return { kind: 'address_contact' };
    }
  }

  // After the address is complete, and before anything else: the verification
  // panel is in the address modal, and telling somebody to prove a number they
  // have not typed yet is not an instruction they can follow.
  if (input.verificationOutstanding) return { kind: 'verify_phone' };

  if (!isDelivery && (!input.firstName.trim() || !input.phone.trim() || !input.phoneValid)) {
    return { kind: 'contact' };
  }

  // Required since the API made it required. A guest order with no address to
  // send the receipt to is an order the customer cannot track, cannot be
  // notified about, and cannot find again.
  if (!input.accountEmail && (!input.email.trim() || !isValidEmail(input.email))) {
    return { kind: 'email' };
  }

  if (input.pricing) return { kind: 'pricing' };
  return { kind: 'ready' };
}

/**
 * What the button does when it is pressed.
 *
 * `submit` hands over to `handleSubmit`. `address` opens the address modal,
 * which is where four of the twelve states are actually resolved — the pin, the
 * line, the name and phone on it, and the verification panel. `reveal` scrolls
 * the offending section into view and flashes it, for the two things that are
 * edited on the page itself.
 */
export type GateAction =
  | { kind: 'submit' }
  | { kind: 'address' }
  | { kind: 'reveal'; field: string };

export interface GateBehaviour {
  /** Dead rather than pressable: there is nothing a press could achieve. */
  disabled: boolean;
  labelKey: string;
  /** Shown when the key is not seeded yet — see `lib/i18n/fallback.ts`. */
  labelFallback: string;
  action: GateAction;
  /**
   * What a deflected press is called in `checkout_error`.
   *
   * Null for the states that submit or are disabled: nothing was refused. The
   * names match the `data-field` keys the validation pass already reports, so
   * the two firing sites produce one groupable field rather than two spellings
   * of every step.
   */
  errorField: string | null;
}

const BEHAVIOUR: Record<CheckoutGate['kind'], GateBehaviour> = {
  submitting: {
    disabled: true,
    labelKey: 'checkout.placing_order',
    labelFallback: 'Placing order…',
    action: { kind: 'submit' },
    errorField: null,
  },
  restoring: {
    disabled: true,
    labelKey: 'common.loading',
    labelFallback: 'Loading…',
    action: { kind: 'submit' },
    errorField: null,
  },
  pickup_branch: {
    disabled: false,
    labelKey: 'checkout.choose_store',
    labelFallback: 'Choose a store',
    action: { kind: 'reveal', field: 'pickupBranch' },
    errorField: 'pickupBranch',
  },
  address: {
    disabled: false,
    labelKey: 'checkout.set_address',
    labelFallback: 'Set address',
    action: { kind: 'address' },
    errorField: 'address',
  },
  unserviceable: {
    disabled: false,
    labelKey: 'checkout.choose_another_address',
    labelFallback: 'Choose another address',
    action: { kind: 'address' },
    errorField: 'unserviceable',
  },
  address_contact: {
    disabled: false,
    labelKey: 'checkout.complete_address_details',
    labelFallback: 'Complete address details',
    action: { kind: 'address' },
    errorField: 'address',
  },
  verify_phone: {
    disabled: false,
    labelKey: 'checkout.verify_your_phone',
    labelFallback: 'Verify your phone',
    action: { kind: 'address' },
    errorField: 'verifyPhone',
  },
  contact: {
    disabled: false,
    labelKey: 'checkout.enter_contact_info',
    labelFallback: 'Enter contact info',
    // The whole section rather than one input: the name and the number are one
    // question, and flashing only the empty half of it reads as a nitpick.
    action: { kind: 'reveal', field: 'contact' },
    errorField: 'firstName',
  },
  email: {
    disabled: false,
    labelKey: 'checkout.enter_email_address',
    labelFallback: 'Enter email address',
    action: { kind: 'reveal', field: 'email' },
    errorField: 'email',
  },
  pricing: {
    disabled: true,
    labelKey: 'checkout.calculating_total',
    labelFallback: 'Calculating total…',
    action: { kind: 'submit' },
    errorField: null,
  },
  pay_now: {
    disabled: false,
    labelKey: 'checkout.pay_now',
    labelFallback: 'Pay now',
    action: { kind: 'submit' },
    errorField: null,
  },
  ready: {
    disabled: false,
    labelKey: 'checkout.place_order',
    labelFallback: 'Place Order',
    action: { kind: 'submit' },
    errorField: null,
  },
};

export function checkoutGateBehaviour(gate: CheckoutGate): GateBehaviour {
  return BEHAVIOUR[gate.kind];
}

type TFunction = (key: string, params?: Record<string, string | number>) => string;

/**
 * The words on the button.
 *
 * Every state but one is a plain key. `ready` is the exception because it
 * quotes money, and the money is the point: a button that says what it is about
 * to charge is the last chance the customer has to disagree with the figure.
 */
export function checkoutGateLabel(gate: CheckoutGate, t: TFunction, total: number): string {
  const behaviour = BEHAVIOUR[gate.kind];
  // The two labels that name a figure. `pay_now` belongs here as much as
  // `ready` does: its copy has carried `{total}` since long before this gate
  // existed, so sending it down the plain path printed the placeholder itself
  // on the button — "Pay Now — {total} AED" — on the one screen where the
  // customer is being asked to hand over money.
  if (gate.kind === 'ready' || gate.kind === 'pay_now') {
    const totalText = total.toFixed(2);
    return withFallback(
      (key) => t(key, { total: totalText }),
      behaviour.labelKey,
      gate.kind === 'ready'
        ? `Place Order · ${totalText} AED`
        : `Pay Now — ${totalText} AED`,
    );
  }
  return withFallback(t, behaviour.labelKey, behaviour.labelFallback);
}
