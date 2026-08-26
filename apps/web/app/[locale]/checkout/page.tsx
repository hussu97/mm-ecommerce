'use client';

import Link from 'next/link';
import { Suspense, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useCart } from '@/lib/cart-context';
import {
  ordersApi, paymentsApi, branchesApi, deliveryApi,
  getSessionId,
} from '@/lib/api';
import { toPaymentMethod, toWireMethod, type PaymentMethod } from '@/lib/types';
import { useAuth } from '@/lib/auth-context';
import { accountEmailOf, ensureCheckoutAuth, isApplePayTestUser } from '@/lib/checkout-auth';
import { Button } from '@/components/ui/Button';
import { SpeedBadge } from '@/components/product/DeliveryEstimate';
import { Input } from '@/components/ui/Input';
import { PhoneInput, isValidPhone } from '@/components/ui/PhoneInput';
import { Spinner } from '@/components/ui/Spinner';
import { useToast } from '@/components/ui/Toast';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics, failureReason } from '@/lib/analytics';
import { DEFAULT_ADDRESS_LABEL } from '@/lib/guest-addresses';
import { withFallback } from '@/lib/i18n/fallback';
import {
  checkoutGateBehaviour, checkoutGateLabel, isValidEmail, resolveCheckoutGate,
} from './checkout-gate';
import { AddressModal, formatAddress, type AddressDraft } from './components/AddressModal';
import { OrderSummary } from './components/OrderSummary';
import { UnavailableItems } from './components/UnavailableItems';
import { PickupBranchPicker } from './components/PickupBranchPicker';
import { ChoiceRow, Section } from './components/Section';
import { UnserviceableNotice } from './components/UnserviceableNotice';
import { PromoCodeStep } from './components/PromoCodeStep';
import { clearCheckoutSession, useCheckoutForm } from './hooks/useCheckoutForm';
import { useApplePay } from './hooks/useApplePay';
import { useOrderPreview } from './hooks/useOrderPreview';
import { usePhoneVerification } from './hooks/usePhoneVerification';
import { useRetryOrder } from './hooks/useRetryOrder';
import { useCartPromoRecovery, usePromoRevalidation } from '@/lib/use-promo-validation';
import type { DeliveryRates, PickupBranch } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

/**
 * Cash only works when the customer comes to the counter, so it is offered for
 * collection and not for delivery. Card works for both.
 *
 * There is one card option, and there is no second one to add: Stripe and Ziina
 * are not choices a customer makes. Which of them settles the card is decided
 * server-side from the `payment_gateways` table, so a processor outage is
 * answered by an admin toggle rather than by a release, and this screen never
 * has to know which one it was.
 */
function paymentOptionsFor(method: 'delivery' | 'pickup'): PaymentMethod[] {
  return method === 'pickup' ? ['cod', 'card'] : ['card'];
}

// ─── Reaching a field ─────────────────────────────────────────────────────────

/**
 * Bring a section into view and put the cursor in it.
 *
 * The button sits below everything, and on a phone it is stuck to the bottom of
 * the screen — so the thing it is complaining about is almost always somewhere
 * the customer cannot see. Scrolling to it is not a nicety; without it the
 * button appears to do nothing at all.
 *
 * Which input inside the section gets the cursor is chosen rather than assumed:
 * the pickup contact section holds a name and a number, and landing on the name
 * when the name is filled in and the number is not is the form pointing at the
 * wrong half of its own complaint. First empty field, else the first field.
 */
function scrollToField(field: string) {
  requestAnimationFrame(() => {
    const el =
      document.querySelector<HTMLElement>(`[data-field="${field}"]`) ??
      document.querySelector<HTMLElement>('[data-field-error="true"]');
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const inputs = Array.from(
      el.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
        'input, select, textarea',
      ),
    );
    (inputs.find((i) => !i.value) ?? inputs[0])?.focus({ preventScroll: true });
  });
}

/** How long a nudged section stays lit. Long enough to find, short enough not to nag. */
const HIGHLIGHT_MS = 1800;

/**
 * The vocabulary the API refuses a coupon in.
 *
 * `ApiError` carries a status and a sentence, and nothing machine-readable that
 * says "this was about the code" — so the sentence is what there is to read.
 * Every one of the server's coupon refusals is in here: not found, not active,
 * not yet valid, expired, usage limit, new customers only, already used, and
 * the minimum-order figure.
 */
const PROMO_REFUSAL = /promo|coupon|discount|this code|new customers|already used|minimum order/i;

/**
 * Is this refusal about the coupon on the basket?
 *
 * Asked only when there is a coupon to refuse, so a sold-out line or a zone
 * rejection never gets printed against the promo field. Deliberately generous
 * about which sentences count: showing a refusal next to the code when it was
 * really about something else costs a customer one glance, while swallowing the
 * one sentence that explains why their discount was rejected costs them the
 * order.
 */
function isPromoRefusal(err: unknown, hasPromo: boolean): boolean {
  if (!hasPromo || !err) return false;
  const status = (err as { status?: number }).status;
  // 400 is what `BadRequestError` becomes; 422 is the schema layer. A 500 or a
  // dropped connection is not a rule being applied to this code.
  if (typeof status === 'number' && status !== 400 && status !== 422) return false;
  return err instanceof Error && PROMO_REFUSAL.test(err.message);
}

/**
 * "Tomorrow, 13:00" — or just "Tomorrow" where an hour would be a lie.
 *
 * The words are translated; the date and the hour are formatted by the browser,
 * which knows the customer's locale and calendar far better than a translation
 * table ever will. Everything is read on the shop's clock rather than the
 * device's: a customer checking out from London is still being delivered to in
 * the UAE, and "tomorrow" has to mean the shop's tomorrow.
 */
const SHOP_TZ = 'Asia/Dubai';

function formatEstimate(
  estimate: { at: string; precision: 'time' | 'day' },
  locale: string,
  t: (k: string, p?: Record<string, string | number>) => string,
): string {
  const at = new Date(estimate.at);
  if (Number.isNaN(at.getTime())) return '';

  const dayKey = (d: Date) =>
    new Intl.DateTimeFormat('en-CA', { timeZone: SHOP_TZ }).format(d);
  const today = dayKey(new Date());
  const tomorrow = dayKey(new Date(Date.now() + 86_400_000));
  const target = dayKey(at);

  const day =
    target === today
      ? t('checkout.delivery_today')
      : target === tomorrow
        ? t('checkout.delivery_tomorrow')
        : new Intl.DateTimeFormat(locale, {
            weekday: 'short',
            day: 'numeric',
            month: 'short',
            timeZone: SHOP_TZ,
          }).format(at);

  if (estimate.precision === 'day') return t('checkout.delivery_by_day', { day });

  const time = new Intl.DateTimeFormat(locale, {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: SHOP_TZ,
  }).format(at);
  return t('checkout.delivery_by_time', { day, time });
}

// ─── Checkout ─────────────────────────────────────────────────────────────────

/**
 * The form, and nothing else.
 *
 * What used to be here and is not any more: the money. This screen computed the
 * grand total twice from different inputs, mirrored the server's small-basket
 * fee rule in TypeScript, and printed a VAT line from a formula that ignored
 * both fees. All four now arrive from `POST /orders/preview` — the same
 * calculation the order is written from — and this component renders them. See
 * `hooks/useOrderPreview.ts`.
 *
 * What is left is composition: four hooks holding the state that has a lifetime
 * (the form and its `sessionStorage` copy, the phone verification, the returned
 * unpaid order, the preview), and the sections that draw it.
 */
function CheckoutContent() {
  const { cart, refreshCart, cartLoaded, cartError } = useCart();
  const { addToast } = useToast();
  const { t, locale } = useTranslation();
  const { user } = useAuth();

  const { form, onChange, savedAddresses, setSavedAddresses } = useCheckoutForm(user);
  const { retryOrder, setRetryOrder, restoring: restoringOrder } = useRetryOrder(addToast, t);
  const { verifiedPhone, setVerifiedPhone } = usePhoneVerification(form.phone);

  const [submitting, setSubmitting] = useState(false);
  const [deliveryRates, setDeliveryRates] = useState<DeliveryRates | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [addressOpen, setAddressOpen] = useState(false);
  /**
   * Why the address sheet was opened, which decides where it lands.
   *
   * "Verify your mobile number" and "change my address" are two different
   * errands that happen to share one sheet, and the sheet cannot tell them
   * apart from `isOpen` alone. Sent along so the verification errand skips the
   * address list — a screen with no verification on it — and arrives at the
   * panel that answers it.
   */
  const [addressIntent, setAddressIntent] = useState<'select' | 'verifyPhone'>('select');
  const [showExtras, setShowExtras] = useState(false);
  /**
   * Whether the test-only Apple Pay option is the one chosen.
   *
   * Held apart from `form.paymentMethod` on purpose: Apple Pay is a *card*, so
   * the order is still written as a card order and the checkout stays
   * provider-agnostic everywhere the money is described. This only changes how
   * the card is collected — an in-page sheet instead of the hosted page — so it
   * is a view-model flag, not a payment method, and never reaches the wire.
   */
  const [applePaySelected, setApplePaySelected] = useState(false);
  const [pickupBranches, setPickupBranches] = useState<PickupBranch[]>([]);
  /**
   * The section the button just pointed at, lit for a moment.
   *
   * Deliberately not the red error state. Nothing is wrong with a field that
   * has simply not been filled in yet, and painting it red for pressing the pay
   * button early is the form scolding somebody for reading it in order. The
   * ring says "here", fades, and leaves no trace; red is reserved for a value
   * that was actually rejected.
   */
  const [highlight, setHighlight] = useState<string | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /**
   * Why `POST /orders` refused the coupon, when it did.
   *
   * Held separately from `errors` because it is not a field on this form that
   * the customer typed wrong — it is the server's verdict on a code, and it has
   * to survive long enough to be read next to that code once the fold-out has
   * been opened for them.
   */
  const [promoRefusal, setPromoRefusal] = useState<string | null>(null);

  const isDelivery = form.deliveryMethod === 'delivery';
  // Where the receipt is already going, when there is an account to read it
  // off. Null for guests — see accountEmailOf.
  const accountEmail = accountEmailOf(user);
  // Who the server should judge a coupon against, as far as this form knows.
  //
  // A new-customer coupon is refused on an account, an email *or* a phone that
  // has ordered before, and `create_order` checks all three. Validating without
  // them asks a different question from the one the pay button is judged on —
  // the discount shows as applied and the order is then refused at the last
  // step, which is both the worst moment to find out and the point at which
  // there is nothing left to do about it.
  const promoIdentity = useMemo(
    () => ({
      email: accountEmail ?? (form.email.trim() || null),
      phone: form.phone.trim() || null,
      // The phone gate is a delivery rule, so the answer depends on this. Sent
      // with the identity because it is part of "who is asking, for what" —
      // without it, a collection is told it needs a verified number it will
      // never actually be asked for.
      delivery_method: form.deliveryMethod,
    }),
    [accountEmail, form.email, form.phone, form.deliveryMethod],
  );

  /**
   * Is this order being held up by an unproved number?
   *
   * All four conditions matter. A collection is never asked (no courier, no
   * cost to protect). A basket with no discount on it has nothing to lose. A
   * coupon without the gate does not care. And a number already proved has
   * already answered.
   */
  const verificationOutstanding =
    isDelivery &&
    form.promoDiscount > 0 &&
    form.promoNeedsVerify &&
    verifiedPhone !== form.phone.trim();
  const paymentOptions = paymentOptionsFor(form.deliveryMethod);
  // Keep the selection legal: switching to delivery must not leave cash chosen.
  const paymentMethod = paymentOptions.includes(form.paymentMethod)
    ? form.paymentMethod
    : paymentOptions[0];

  useEffect(() => {
    // Only for the small-basket fee's explanation, which quotes the threshold
    // and the amount. The fee that is actually *charged* comes back on the
    // preview; these two are the copy around it.
    deliveryApi.getRates().then(setDeliveryRates).catch(() => { /* the line simply omits them */ });
  }, []);

  // Loaded up front rather than when collection is chosen, so the list is
  // already there the moment somebody switches — a spinner between "I'll
  // collect" and "from where?" is a spinner in the middle of one decision.
  useEffect(() => {
    let cancelled = false;
    branchesApi
      .pickupPoints()
      .then((list) => { if (!cancelled) setPickupBranches(list); })
      .catch(() => { /* the picker says so, and delivery is unaffected */ });
    return () => { cancelled = true; };
  }, []);

  // One branch is not a choice, so it is made. Two or more is, and is left
  // blank on purpose: preselecting one is how somebody drives to the wrong
  // city.
  //
  // Derived at render rather than written into the form by an effect. The form
  // is the record of what the customer chose and is persisted as such; a value
  // nobody picked has no business surviving a trip to the payment gateway, and
  // the effect that used to put it there wrote to the form from inside a
  // response handler for no gain.
  const pickupBranchId =
    form.pickupBranchId || (pickupBranches.length === 1 ? pickupBranches[0].id : '');

  const clearError = useCallback((key: string) => {
    setErrors((prev) => { const next = { ...prev }; delete next[key]; return next; });
  }, []);

  /**
   * Scroll to a section, light it up, and let go of it again.
   *
   * The one place both halves of "show me the problem" live: the button's own
   * deflection and the validation pass's first failing field go through here,
   * so a customer gets the same gesture whichever of the two spoke.
   */
  const revealField = useCallback((field: string) => {
    scrollToField(field);
    setHighlight(field);
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlight(null), HIGHLIGHT_MS);
  }, []);

  // A timer that outlives the page would call `setHighlight` on nothing.
  useEffect(() => () => { if (highlightTimer.current) clearTimeout(highlightTimer.current); }, []);

  /** Open the address sheet, saying what it was opened for. */
  const openAddress = useCallback((intent: 'select' | 'verifyPhone' = 'select') => {
    setAddressIntent(intent);
    setAddressOpen(true);
  }, []);

  /**
   * Take the customer to a coupon the server just refused.
   *
   * An effect rather than a call in the catch block, because the same failure
   * opens the "add promo or note" fold-out — and a section that has not been
   * rendered yet cannot be scrolled to. This runs after it has.
   *
   * Scroll only, no ring: the refusal itself lands in red, against the code,
   * with `role="alert"` on it. Lighting the section up as well would be the
   * second thing on screen shouting about the same sentence.
   */
  useEffect(() => {
    if (promoRefusal) scrollToField('promo');
  }, [promoRefusal]);

  const subtotal = cart?.subtotal ?? 0;

  // Keep `form.promoDiscount` — the number the pay button was quoted against —
  // equal to what the order will actually be written with. This runs for as
  // long as the page does, not just while the promo fold-out is open: the
  // discount was applied on the basket page against that page's subtotal and an
  // email-only identity, and the basket can go on changing in another tab while
  // this form is being filled in. Disabled for a returned unpaid order, whose
  // promo is already priced into an order that exists (and while one is still
  // being looked up); disabled too until the basket has actually loaded —
  // before that `subtotal` reads 0, and re-checking a real discount against a
  // basket we have not seen yet would take it off for a reason that was never
  // true.
  //
  // It survives the preview endpoint, which now returns the authoritative
  // discount and would make a *displayed* number redundant. What it does that
  // the preview does not is act: it takes a refused code off the form, tells
  // the customer why, and carries the phone-gate flag the address panel needs.
  // The preview reports; this decides.
  // The code the basket applied, when nothing carried it here. `sessionStorage`
  // is allowed to fail and did: on 20 August a basket reading 29.75 became a
  // checkout reading 35.00 with an empty promo field, and the order would have
  // been submitted at full price. The cart remembers the code server-side now,
  // and this validates it afresh rather than trusting a carried number.
  //
  // Ordered before the re-validation below because it is what *puts* a code on
  // the form; that one keeps an existing code honest.
  useCartPromoRecovery({
    cartCode: cart?.promo_code,
    formCode: form.promoCode,
    subtotal,
    identity: promoIdentity,
    enabled: !retryOrder && !restoringOrder && cartLoaded && subtotal > 0,
    onRecovered: (outcome) => {
      onChange({
        promoCode: outcome.code,
        promoDiscount: outcome.discount,
        promoMessage: outcome.message,
        promoNeedsVerify: outcome.needsVerify,
      });
      // The recovery is silent to the customer, so this is the only trace it
      // leaves. Each one is a discount that would have been lost between the
      // two screens; the rate against `promo_applied` is how often the
      // `sessionStorage` handoff actually fails.
      analytics.promoRecovered({
        code: outcome.code,
        discount: outcome.discount,
        subtotal,
      });
    },
  });

  usePromoRevalidation({
    code: form.promoCode,
    discount: form.promoDiscount,
    subtotal,
    identity: promoIdentity,
    enabled: !retryOrder && !restoringOrder && cartLoaded && subtotal > 0,
    fallbackReason: t('checkout.invalid_promo'),
    onResult: (outcome) => {
      if (outcome.kind === 'applied') {
        // Usually a no-op; the case that matters is a capped percentage
        // discount whose amount moved with the subtotal.
        onChange({
          promoCode: outcome.code,
          promoDiscount: outcome.discount,
          promoMessage: outcome.message,
          promoNeedsVerify: outcome.needsVerify,
        });
      } else if (outcome.kind === 'invalid') {
        // The code comes off, and the customer is told so — the fold-out that
        // used to show this refusal may never have been opened, and a total
        // that quietly grew is worse than a toast. The server's own reason
        // where it gave one, because "no longer valid" without a why reads as
        // a bug rather than a rule.
        onChange({ promoCode: outcome.code, promoDiscount: 0, promoMessage: '', promoNeedsVerify: false });
        addToast(outcome.message ?? t('checkout.invalid_promo'), 'warning');
      }
      // `error` (the request itself failed) deliberately changes nothing: the
      // server re-validates at order creation regardless, and dropping a
      // legitimate discount because one background request timed out is the
      // worse of the two failures.
    },
  });

  // Every figure on this screen, priced by the server. Re-asked whenever any
  // input to it moves — the basket, the pin, the method, the coupon, the
  // identity the coupon is judged against — and debounced, because half of
  // those are fields being typed into.
  const { preview, pricing: previewInFlight } = useOrderPreview({
    enabled: !retryOrder && !restoringOrder && cartLoaded,
    deliveryMethod: form.deliveryMethod,
    latitude: form.locationLat,
    longitude: form.locationLng,
    address: form.addressLine1,
    // The code the order will actually send, so the preview prices what the
    // button will submit. A typed-but-unapplied code is not on the basket.
    promoCode: form.promoDiscount > 0 ? form.promoCode : '',
    email: promoIdentity.email,
    phone: promoIdentity.phone,
    subtotal,
    itemCount: cart?.items.length ?? 0,
  });

  // Read off the preview's delivery block, which was priced in the same call as
  // the total — so the fee on this line and the fee inside that total are one
  // number rather than two that have to agree.
  const quote = preview?.delivery ?? null;
  const freeThreshold = quote?.free_threshold ?? null;
  // The server decides this, not the basket. Free delivery only reaches the
  // zones we price ourselves, so "big enough order" is a necessary condition
  // and not a sufficient one — working it out here from the subtotal alone
  // would promise it to every address in the country.
  const freeApplied = quote?.free_delivery_applied ?? false;
  // Undefined until the first preview lands. Assumed available so the upsell is
  // not suppressed on a cold page; the copy for that state says "in selected
  // areas", which is exactly what we know at that point.
  const freeAvailable = quote?.free_delivery_available ?? true;
  const remainingForFree = quote?.remaining_for_free ?? 0;
  // The question every shopper actually has. Only ever rendered from what the
  // server sent — the schedule that produces it is not something the browser
  // can or should reconstruct.
  const arrival =
    isDelivery && !retryOrder && quote?.delivery_estimate
      ? formatEstimate(quote.delivery_estimate, locale, t)
      : '';

  // Priced off the pin against the active zone map. Close to the kitchen that is
  // a published flat fee; beyond it, the courier's own price for this exact
  // trip. Which means the number is not knowable before the pin is, so until
  // then there is no fee on screen rather than a placeholder we would have to
  // take back — a total that rises after the customer has read it is the worse
  // surprise. It also means the pin can have no price at all, which is not a
  // free delivery and not a zero: it is an address we cannot serve.
  const hasPin = form.locationLat !== null && form.locationLng !== null;
  const unserviceable = isDelivery && !retryOrder && quote?.serviceable === false;
  // What the branch serving this pin has run out of. Empty on an ordinary day.
  //
  // Never on a retry: that order is already written, its lines are already
  // claimed, and what it needs is paying for rather than editing. Offering to
  // remove an item from a basket that no longer governs it would be a button
  // that changes nothing the customer is about to pay.
  const unavailableItems = retryOrder ? [] : (preview?.unavailable_items ?? []);
  const baseFee = quote?.base_fee ?? null;
  // The delivery *option's* price, which the delivery row shows even while
  // collection is selected — the preview prices the pin either way, exactly as
  // the quote it replaced did.
  const homeDeliveryFee = unserviceable ? null : (quote?.delivery_fee ?? null);
  const knowsFee = homeDeliveryFee !== null;

  // The one number the button quotes, and it is the server's. Before the first
  // preview lands it falls back to the basket's own subtotal — not a
  // calculation, just the figure this screen has always shown on its first
  // paint, when no fee, surcharge or discount was known yet either.
  const total = retryOrder ? Number(retryOrder.total) : (preview?.total ?? subtotal);
  const lowOrderFee = retryOrder
    ? Number(retryOrder.low_order_fee ?? 0)
    : (preview?.low_order_fee ?? 0);

  // The test-only in-page Apple Pay path. `enabled` is the account gate — false
  // for a guest and for every non-allowlisted customer, so the hook does no
  // Stripe work and offers nothing for them. `available` narrows that further
  // to a device and a gateway that can actually take it; the option below and
  // the button lower down both hang off it, so on any browser that cannot do
  // Apple Pay neither ever renders. A returned unpaid order replays its own
  // gateway, so Apple Pay is not offered on that path.
  const applePayGated = isApplePayTestUser(user) && !retryOrder;
  const applePay = useApplePay({ enabled: applePayGated, amount: total });
  const applePayOffered = applePayGated && applePay.available;
  // Keep the selection legal: if Apple Pay stops being offered, fall back to
  // the card/cash choice the form already holds.
  const isApplePay = applePaySelected && applePayOffered;
  const selectedPayment: 'card' | 'cod' | 'apple_pay' = isApplePay ? 'apple_pay' : paymentMethod;

  /**
   * The three states worth a goal of their own, each fired once per checkout.
   *
   * They are all derivable from `delivery_quote` above, but a derivable fact is
   * not a funnel step: Umami matches goals and funnel steps on the event name,
   * so "how many baskets hit an address we cannot serve" only becomes a number
   * you can watch, and a step you can measure drop-off across, if it is named.
   * The refs make each of them once-per-visit rather than once-per-keystroke.
   */
  const seenUnserviceable = useRef(false);
  const seenFreeUnlocked = useRef(false);
  const seenLowOrderFee = useRef(false);

  useEffect(() => {
    if (unserviceable && !seenUnserviceable.current) {
      seenUnserviceable.current = true;
      analytics.deliveryUnserviceable({
        subtotal,
        item_count: cart?.items.length ?? 0,
      });
    }
    if (freeApplied && !seenFreeUnlocked.current && freeThreshold !== null) {
      seenFreeUnlocked.current = true;
      analytics.freeDeliveryUnlocked({
        threshold: freeThreshold,
        subtotal: preview ? preview.subtotal - preview.discount_amount : subtotal,
        surface: 'checkout',
      });
    }
    if (lowOrderFee > 0 && !seenLowOrderFee.current && !retryOrder) {
      seenLowOrderFee.current = true;
      analytics.lowOrderFeeApplied({ fee: lowOrderFee, subtotal });
    }
  }, [unserviceable, freeApplied, freeThreshold, lowOrderFee, subtotal, preview, cart, retryOrder]);

  /**
   * Arriving at the checkout at all, with the basket that arrived with them.
   *
   * The main funnel stepped through `/*​/checkout` as a page view, which cannot
   * tell a guest from a signed-in customer, cannot see the basket, and counts a
   * cancelled payment coming back from the gateway as a fresh arrival.
   */
  const checkoutViewed = useRef(false);
  useEffect(() => {
    if (checkoutViewed.current || !cart || cart.items.length === 0) return;
    checkoutViewed.current = true;
    analytics.viewCheckout({
      item_count: cart.items.length,
      subtotal: cart.subtotal ?? 0,
      is_guest: !user || Boolean(user.is_guest),
      has_saved_address: savedAddresses.length > 0,
    });
  }, [cart, user, savedAddresses.length]);

  // The checkout that never rendered a form at all — the basket could not be
  // read. `api_error` catches the request; this catches the outcome, which is a
  // customer looking at a "try again" screen instead of a place-order button.
  const loadFailureReported = useRef(false);
  useEffect(() => {
    if (!cartError || loadFailureReported.current) return;
    loadFailureReported.current = true;
    analytics.checkoutLoadFailed();
  }, [cartError]);

  const currentDraft: AddressDraft = {
    id: form.selectedAddressId,
    label: form.addressLabel || DEFAULT_ADDRESS_LABEL,
    firstName: form.firstName,
    lastName: form.lastName,
    phone: form.phone,
    addressLine1: form.addressLine1,
    addressLine2: form.addressLine2,
    unitNumber: form.unitNumber,
    latitude: form.locationLat,
    longitude: form.locationLng,
  };
  const hasAddress = Boolean(form.addressLine1.trim());

  const applyDraft = (d: AddressDraft) => {
    onChange({
      selectedAddressId: d.id,
      addressLabel: d.label,
      firstName: d.firstName,
      lastName: d.lastName,
      phone: d.phone,
      addressLine1: d.addressLine1,
      addressLine2: d.addressLine2,
      unitNumber: d.unitNumber,
      locationLat: d.latitude,
      locationLng: d.longitude,
    });
    ['address', 'firstName', 'lastName', 'phone'].forEach(clearError);
  };

  /**
   * The order the form describes, in the shape `POST /orders` takes.
   *
   * Extracted so the place-order path and the in-page Apple Pay path build the
   * *same* order rather than two literals that could drift — Apple Pay is a
   * card, so `payment_method` is the same `card`→`stripe` wire word either way.
   */
  const buildOrderCreate = () => ({
    // Blank means "no email" — the API falls back to the session's own
    // address rather than refusing the order.
    email: accountEmail ?? (form.email.trim() ? form.email.trim().toLowerCase() : undefined),
    delivery_method: form.deliveryMethod,
    shipping_address: isDelivery
      ? {
          label: form.addressLabel || DEFAULT_ADDRESS_LABEL,
          first_name: form.firstName,
          last_name: form.lastName,
          phone: form.phone,
          address_line_1: form.addressLine1,
          address_line_2: form.addressLine2 || undefined,
          unit_number: form.unitNumber || undefined,
          // Non-null for a delivery: `checkout-gate` returns `address`
          // and the button stays unpressable until a pin is dropped.
          latitude: form.locationLat as number,
          longitude: form.locationLng as number,
        }
      : undefined,
    pickup_branch_id: isDelivery ? undefined : pickupBranchId || undefined,
    // Stamped on the order, and every email about it is written in it.
    locale,
    promo_code: form.promoDiscount > 0 ? form.promoCode : undefined,
    // The legacy word, for one release. See `toWireMethod`.
    payment_method: toWireMethod(paymentMethod),
    notes: form.notes || undefined,
    session_id: getSessionId() ?? undefined,
  });

  /**
   * Write the order the form describes and clean up after it.
   *
   * The half of `handleSubmit` the Apple Pay path also needs — everything up to
   * an order existing, and nothing about the gateway. Throws on failure so the
   * caller decides what a failure means (a toast here, a closed sheet there).
   */
  const createOrderFromForm = async (): Promise<import('@/lib/types').Order> => {
    if (!user) await ensureCheckoutAuth(user);
    if (!cart || cart.items.length === 0) {
      analytics.checkoutCartEmpty();
      throw new Error(t('checkout.cart_empty'));
    }
    const order = await ordersApi.create(buildOrderCreate());
    clearCheckoutSession();
    await refreshCart();
    return order;
  };

  /**
   * Take the payment in-page with Apple Pay, then land on the confirmation.
   *
   * Its own path rather than a branch of `handleSubmit`, because the Apple Pay
   * sheet must be opened synchronously from the press to count as a user
   * gesture — there is no room to create the order first. So `pay()` shows the
   * sheet now and writes the order inside its authorised callback. Only reached
   * when the gate is already `ready`, so the form is complete and
   * `createOrderFromForm` should not be refused by our own validation.
   */
  const handleApplePay = () => {
    setSubmitting(true);
    setPromoRefusal(null);
    analytics.checkoutStepComplete({ step: 1, delivery_method: form.deliveryMethod });
    applePay.pay({
      total,
      createOrder: createOrderFromForm,
      onSuccess: (order) => {
        const orderEmail =
          order.email ?? accountEmail ?? form.email.trim().toLowerCase();
        window.location.assign(
          `/${locale}/checkout/confirmation?order_number=${order.order_number}&email=${encodeURIComponent(orderEmail)}`,
        );
      },
      onError: (message) => {
        setSubmitting(false);
        // Empty message = the customer dismissed the sheet; nothing was refused,
        // so nothing is said.
        if (message) addToast(message, 'error');
      },
    });
  };

  /**
   * No `useCallback`, and deliberately.
   *
   * This was memoized by hand against a seventeen-entry dependency array — one
   * entry per thing the checkout knows — which is a list nobody can keep
   * correct and which the React Compiler could not preserve anyway: several of
   * those values are now passed to components in other files, so it cannot
   * prove they are not mutated and skips compiling the whole component. The
   * compiler memoizes this for us, correctly, from what the function actually
   * reads.
   */
  const handleSubmit = async () => {
    if (!retryOrder) {
      // Filled in the order the page is read, because `fields[0]` is what the
      // customer is scrolled to. Email used to be checked first and sat at the
      // top of this object, so a form missing both an address and an email
      // scrolled past the address to complain about the email.
      const found: Record<string, string> = {};

      if (isDelivery) {
        if (!form.addressLine1.trim()) found.address = t('checkout.address_required');
        // The pin, which nothing on this screen ever checked for. `OrderCreate`
        // wants latitude and longitude, the courier cannot be asked to price a
        // trip without them, and an address typed without touching the map got
        // all the way to the payment page before anyone said so.
        else if (!hasPin) {
          found.address = withFallback(
            t,
            'checkout.address_pin_required',
            'Please drop a pin on the map so we know where to deliver.',
          );
        } else if (!form.firstName.trim() || !form.phone.trim() || !isValidPhone(form.phone)) {
          found.address = t('checkout.address_contact_incomplete');
        } else if (unserviceable) {
          // The API refuses this too. Stopping here saves the customer a round
          // trip and a payment page they were never going to get through.
          found.unserviceable = t('checkout.unserviceable_title');
        } else if (verificationOutstanding) {
          // Checked after the address is complete, because the way to fix it is
          // to open the address form — and telling somebody to go and verify a
          // number they have not typed yet is not an instruction they can
          // follow. The API refuses this too; stopping here means the refusal
          // arrives next to the button that resolves it rather than as a toast
          // on a form the customer has already left.
          found.verifyPhone = t('checkout.verify_phone_required');
        }
      } else {
        // Which counter, before who is collecting from it — that is the order
        // the two sections appear in, and this object's order decides which one
        // the customer is taken to.
        //
        // Only when there is a list to choose from. If the branches could not be
        // loaded the order still goes through and the API resolves one, because
        // losing a paid sale to a failed GET is the worse trade.
        if (pickupBranches.length > 0 && !pickupBranchId) {
          found.pickupBranch = t('checkout.pickup_branch_required');
        }
        if (!form.firstName.trim()) found.firstName = t('checkout.first_name_required');
        if (!form.phone.trim() || !isValidPhone(form.phone)) found.phone = t('checkout.valid_phone_required');
      }

      // Required now, not merely checked-if-typed. `OrderCreate` demands an
      // email, so a blank one is no longer a customer who chose not to give us
      // one — it is an order the API will refuse after the payment page. Blank
      // and malformed are told apart because they are different mistakes and
      // the fix for each is a different sentence. Nothing to check at all when
      // the address came from the account rather than the keyboard.
      if (!accountEmail) {
        if (!form.email.trim()) {
          found.email = withFallback(t, 'checkout.email_required', 'Email address is required');
        } else if (!isValidEmail(form.email)) {
          found.email = t('checkout.valid_email_required');
        }
      }

      if (Object.keys(found).length > 0) {
        const fields = Object.keys(found);
        setErrors(found);
        analytics.checkoutError({
          step: 1,
          field: fields[0],
          // The whole set, not just the first. A form failing on one field is a
          // typo; the same three fields failing together is a form problem.
          //
          // Sorted on a *copy*. `Array.prototype.sort` sorts in place, so this
          // used to reorder `fields` before the line below read it — and a
          // collection missing both a branch and a name was scrolled to the
          // name, because `firstName` sorts ahead of `pickupBranch`.
          fields: [...fields].sort().join(','),
          error_count: fields.length,
          delivery_method: form.deliveryMethod,
        });
        // Nothing left but the SMS: put the panel that sends it in front of
        // them rather than a ring around a link to it. The press was "place my
        // order" and this is the whole of what stands between the two.
        // Only when it is the *only* thing outstanding — a sheet over an email
        // field that is also empty hides the other half of the answer.
        if (fields.length === 1 && fields[0] === 'verifyPhone') openAddress('verifyPhone');
        revealField(fields[0]);
        return;
      }
      setErrors({});
    }

    // Last attempt's verdict on the coupon, cleared before this one asks again.
    setPromoRefusal(null);
    setSubmitting(true);
    // Which half of this we got to, so a failure below can say whether the order
    // was ever written. The two are entirely different problems: nothing was
    // created, versus an order exists and cannot be paid for.
    let stage: 'create_order' | 'create_session' = 'create_order';
    let createdOrder: import('@/lib/types').Order | null = null;
    try {
      if (!user) await ensureCheckoutAuth(user);

      let orderNumber: string;
      if (retryOrder) {
        orderNumber = retryOrder.order_number;
        analytics.paymentRetry({
          order_number: retryOrder.order_number,
          provider: retryOrder.payment_provider ?? undefined,
        });
      } else {
        if (!cart || cart.items.length === 0) {
          analytics.checkoutCartEmpty();
          addToast(t('checkout.cart_empty'), 'error');
          setSubmitting(false);
          return;
        }

        const order = await ordersApi.create(buildOrderCreate());
        createdOrder = order;
        orderNumber = order.order_number;

        clearCheckoutSession();
        await refreshCart();
      }

      // Past this line the order exists; anything that fails now is the gateway.
      stage = 'create_session';
      // A retry replays the order's own method, and an order from before the
      // method/gateway split carries `stripe` there — read as `card`.
      const method = retryOrder ? toPaymentMethod(retryOrder.payment_method) : paymentMethod;
      const session = await paymentsApi.createSession(orderNumber, method);

      analytics.checkoutStepComplete({ step: 1, delivery_method: form.deliveryMethod });

      // Cash and zero-total orders are confirmed server-side — there is no
      // gateway to visit, so go straight to the confirmation.
      if (session.confirmed) {
        const orderEmail =
          createdOrder?.email ?? retryOrder?.email ?? accountEmail ?? form.email.trim().toLowerCase();
        // `assign` rather than writing `location.href`. Same navigation; the
        // assignment reads as mutating a value from outside the component,
        // which the React Compiler refuses in a function it is compiling.
        window.location.assign(
          `/${locale}/checkout/confirmation?order_number=${orderNumber}&email=${encodeURIComponent(orderEmail)}`,
        );
        return;
      }
      window.location.assign(session.checkout_url!);
    } catch (err) {
      // Keep a created order so payment can be retried without a cart.
      if (createdOrder) setRetryOrder(createdOrder);
      const message = err instanceof Error ? err.message : 'Something went wrong. Please try again.';
      const reason = failureReason(err);

      // An order that was never written is not a payment failure, and counting
      // it as one is how a promo rule that refuses every basket hides inside the
      // gateway's numbers. `create_order` is refused by our own API — a coupon
      // that no longer applies, an address the zone map rejects, a sold-out
      // line — and it needs its own event and its own name.
      if (stage === 'create_order') {
        analytics.orderCreateFailed({
          reason,
          status: (err as { status?: number }).status,
          delivery_method: form.deliveryMethod,
          total,
          has_promo: form.promoDiscount > 0,
        });
      } else {
        analytics.paymentFailed({
          order_number: createdOrder?.order_number ?? retryOrder?.order_number ?? '',
          error_message: message,
          reason,
          // The gateway that actually failed, when the order got far enough
          // to have one. That is the value worth alerting on: `card` says a
          // payment broke, `stripe` says which processor broke it.
          provider: retryOrder?.payment_provider ?? paymentMethod,
          total,
          stage,
        });
      }
      // The server's backstop for the phone gate, caught by its own words.
      //
      // The check above this normally stops it, but it reads a flag carried
      // from wherever the code was applied — and that flag can be stale: a
      // customer who verified, changed their number, and came back. The API is
      // the one that decides, so when it says this, put the form back into the
      // state that offers the fix rather than leaving a toast in front of a
      // button that will keep failing.
      if (stage === 'create_order' && /verify your mobile number/i.test(message)) {
        // Through `onChange` rather than a bare `setForm`, so the flag survives
        // the trip to the gateway like every other field on this form. It used
        // to be the one write that skipped the `sessionStorage` copy.
        onChange({ promoNeedsVerify: true });
        setErrors({ verifyPhone: t('checkout.verify_phone_required') });
        openAddress('verifyPhone');
        // Behind the sheet, so closing it lands on the line that explains why.
        revealField('verifyPhone');
      } else if (stage === 'create_order' && isPromoRefusal(err, form.promoDiscount > 0)) {
        // A coupon refused at the last step, said where the coupon is.
        //
        // The API answers each refusal specifically — "This code is for new
        // customers only", "You have already used this promo code", a minimum
        // order figure — and that sentence is the only thing that tells the
        // customer what to do next. A toast is not enough on its own: it is
        // gone in seconds, and the code it is about is folded away behind "add
        // promo or note", which most customers never opened and which a
        // customer who applied the code in the basket never had to. So the
        // fold-out is opened for them and the refusal is printed against the
        // code, next to the button that removes it.
        setShowExtras(true);
        setPromoRefusal(message);
      }
      addToast(message, 'error');
      setSubmitting(false);
    }
  };

  // ── Non-form states ────────────────────────────────────────────────────────

  if (cartError && !submitting && !retryOrder) {
    return (
      <div className="max-w-md mx-auto px-4 py-16 flex flex-col items-center text-center gap-4">
        <Icon name="wifi_off" className="text-5xl text-secondary" />
        <h1 className="font-display text-2xl text-primary uppercase tracking-widest">
          {t('checkout.cart_load_failed')}
        </h1>
        <Button variant="primary" onClick={() => refreshCart()}>{t('common.try_again')}</Button>
        <Link href={`/${locale}/cart`} className="font-body text-sm text-gray-500 underline">
          {t('breadcrumb.cart')}
        </Link>
      </div>
    );
  }

  if (!cart && !cartLoaded && !submitting) {
    return (
      <div className="max-w-md mx-auto px-4 py-20 flex flex-col items-center gap-4">
        <Spinner size="lg" />
        <p className="font-body text-sm text-gray-400">{t('checkout.loading_cart')}</p>
      </div>
    );
  }

  if (cart && cart.items.length === 0 && !submitting && !retryOrder && !restoringOrder) {
    return (
      <div className="max-w-md mx-auto px-4 py-16 flex flex-col items-center text-center gap-4">
        <Icon name="shopping_bag" className="text-5xl text-secondary" />
        <h1 className="font-display text-2xl text-primary uppercase tracking-widest">{t('checkout.cart_empty')}</h1>
        <Link href={`/${locale}`}><Button variant="primary">{t('cart.continue_shopping')}</Button></Link>
      </div>
    );
  }

  // ── The page ───────────────────────────────────────────────────────────────

  /**
   * What the button is for, right now — see `checkout-gate.ts`.
   *
   * One resolution, read by both render sites. The two used to carry the same
   * hand-written ternary and the same `opacity-60`, which is two places for one
   * decision to be changed in and, on a phone, the one of the two that is
   * actually on screen.
   */
  const gate = resolveCheckoutGate({
    submitting,
    restoring: restoringOrder,
    hasUnpaidOrder: Boolean(retryOrder),
    deliveryMethod: form.deliveryMethod,
    branchesLoaded: pickupBranches.length > 0,
    pickupBranchId,
    addressLine1: form.addressLine1,
    hasPin,
    unserviceable: Boolean(unserviceable),
    unavailableCount: unavailableItems.length,
    firstName: form.firstName,
    phone: form.phone,
    phoneValid: isValidPhone(form.phone),
    verificationOutstanding,
    accountEmail,
    email: form.email,
    // Only while there is nothing usable to quote. A stale total beats a button
    // that says "Calculating total…" every time a character is typed into the
    // address line — see `OrderPreviewState.pricing`.
    pricing: previewInFlight && preview === null,
  });
  const gateBehaviour = checkoutGateBehaviour(gate);
  const gateLabel = checkoutGateLabel(gate, t, total);

  /**
   * Do what the button says, rather than validating and reporting.
   *
   * Three outcomes: submit, open the address modal, or take the customer to the
   * section that is not finished. `handleSubmit` is still the one that decides
   * whether the order is really allowed — this only decides which of the three
   * a press means.
   */
  const onPlaceOrder = () => {
    const { action, errorField } = gateBehaviour;
    if (action.kind === 'submit') {
      void handleSubmit();
      return;
    }
    // The same event the validation pass fires, from the other half of the same
    // decision. Without it every deflected press would be invisible and
    // `checkout_error` would go quiet the moment the gate started catching
    // things before the validation could — which would read, in the dashboard,
    // as a checkout that stopped having problems.
    if (errorField) {
      analytics.checkoutError({
        step: 1,
        field: errorField,
        fields: errorField,
        error_count: 1,
        delivery_method: form.deliveryMethod,
      });
    }
    if (action.kind === 'address') {
      // Two different reasons share this action. "Choose another address" and
      // "Complete address details" want the list; "Verify your phone" wants the
      // panel that does it, which is on the form and never on the list.
      openAddress(errorField === 'verifyPhone' ? 'verifyPhone' : 'select');
      return;
    }
    revealField(action.field);
  };

  const placeOrderButton = (
    <Button
      variant="primary"
      size="lg"
      fullWidth
      onClick={onPlaceOrder}
      loading={submitting}
      disabled={gateBehaviour.disabled}
    >
      {gateLabel}
    </Button>
  );

  /**
   * The Apple-styled button that replaces "Place Order" when Apple Pay is the
   * chosen method and everything else is done.
   *
   * Only when the gate is `ready` (or already `submitting` this same payment):
   * before that the ordinary button is the right one, because it walks the
   * customer down the page to whatever is still missing — an Apple Pay sheet
   * over an unfinished form would open onto an order that cannot be written.
   */
  const showApplePayButton = isApplePay && (gate.kind === 'ready' || gate.kind === 'submitting');
  const applePayButton = (
    <button
      type="button"
      onClick={handleApplePay}
      disabled={submitting}
      aria-label={withFallback(t, 'checkout.pay_with_apple_pay', 'Pay with Apple Pay')}
      className="w-full h-12 flex items-center justify-center gap-1.5 rounded-md bg-black text-white font-medium transition-opacity hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed"
    >
      {submitting ? (
        <Spinner size="sm" />
      ) : (
        <>
          {/* The Apple logo, drawn rather than an icon-font glyph so it renders
              the same on every device the sheet will open on. */}
          <svg viewBox="0 0 24 24" aria-hidden="true" className="w-5 h-5 fill-current" focusable="false">
            <path d="M17.05 12.54c-.02-2.02 1.65-2.99 1.73-3.04-.94-1.38-2.41-1.57-2.93-1.59-1.25-.13-2.44.73-3.07.73-.63 0-1.61-.71-2.65-.69-1.36.02-2.62.79-3.32 2.01-1.42 2.46-.36 6.1 1.02 8.1.67.98 1.47 2.08 2.52 2.04 1.01-.04 1.39-.65 2.61-.65 1.22 0 1.56.65 2.63.63 1.09-.02 1.78-1 2.45-1.98.77-1.13 1.09-2.23 1.11-2.29-.02-.01-2.13-.82-2.15-3.25zM15.03 6.6c.56-.68.94-1.62.83-2.56-.81.03-1.79.54-2.37 1.21-.52.6-.97 1.56-.85 2.48.9.07 1.83-.46 2.39-1.13z"/>
          </svg>
          <span className="text-base">{withFallback(t, 'checkout.apple_pay', 'Pay')}</span>
        </>
      )}
    </button>
  );

  /** Whichever button this checkout is currently offering. */
  const actionButton = showApplePayButton ? applePayButton : placeOrderButton;

  /** The momentary "it's this one" ring — never the red of a rejected value. */
  const highlightRing = (field: string) =>
    highlight === field
      ? 'rounded-sm ring-2 ring-primary/70 ring-offset-4 ring-offset-white transition-shadow duration-300'
      : 'transition-shadow duration-300';

  return (
    <div className="max-w-xl mx-auto px-4 py-8 sm:py-10">
      <header className="flex items-baseline justify-between mb-6">
        <h1 className="font-display text-2xl sm:text-3xl text-primary uppercase tracking-[0.15em]">
          {t('breadcrumb.checkout')}
        </h1>
        <Link href={`/${locale}/cart`} className="font-body text-xs text-gray-400 hover:text-primary transition-colors">
          {t('breadcrumb.cart')}
        </Link>
      </header>

      {/* 1 — How they want it. Decides everything below. */}
      <Section label={t('checkout.delivery_method')}>
        <div className="space-y-2">
          <ChoiceRow
            selected={isDelivery}
            onSelect={() => {
              analytics.selectDeliveryMethod({ method: 'delivery', fee: homeDeliveryFee ?? 0 });
              onChange({ deliveryMethod: 'delivery' });
            }}
            icon="local_shipping"
            title={t('checkout.delivery_option')}
            subtitle={unserviceable
              ? t('checkout.unserviceable_short')
              : freeApplied
                ? t('checkout.free_delivery_qualified')
                : !hasPin
                  ? t('checkout.fee_from_address')
                  : !freeAvailable
                    // The fee here is a courier bill, not a number we set, so
                    // there is nothing to earn by spending more.
                    ? t('checkout.free_delivery_not_in_area')
                    : t('checkout.free_delivery_upsell', { amount: remainingForFree.toFixed(2) })}
            trailing={
              unserviceable ? (
                <Icon name="wrong_location" className="text-lg text-amber-600" />
              ) : !knowsFee ? (
                <span className="text-gray-400">—</span>
              ) : freeApplied && (baseFee ?? 0) > 0 ? (
                // The saving is the point, so show what was avoided.
                <span className="flex items-center gap-2">
                  <span className="text-gray-400 line-through">{(baseFee ?? 0).toFixed(2)} AED</span>
                  <span className="text-green-600 font-medium">{t('common.free')}</span>
                </span>
              ) : homeDeliveryFee === 0 ? (
                <span className="text-green-600">{t('common.free')}</span>
              ) : (
                <span className="text-gray-700">{homeDeliveryFee.toFixed(2)} AED</span>
              )
            }
          />
          <div>
            <ChoiceRow
              selected={!isDelivery}
              onSelect={() => {
                analytics.selectDeliveryMethod({ method: 'pickup', fee: 0 });
                onChange({ deliveryMethod: 'pickup' });
              }}
              icon="storefront"
              title={t('checkout.store_pickup')}
              subtitle={t('checkout.pickup_description')}
              trailing={<span className="text-green-600">{t('common.free')}</span>}
            />
          </div>
        </div>
      </Section>

      {/* 1b — Which counter. Only a question when collection is chosen, and it
          sits directly under the choice that raised it rather than further down
          the form, because it is part of the same decision. */}
      {!isDelivery && !retryOrder && (
        <Section label={t('checkout.pickup_branch')}>
          {/* The ring goes on the outside; `data-field` is inside the picker,
              where the scroll needs it. */}
          <div className={highlightRing('pickupBranch')}>
            <PickupBranchPicker
              branches={pickupBranches}
              selectedId={pickupBranchId}
              onSelect={(id) => {
                const branch = pickupBranches.find((b) => b.id === id);
                if (branch) analytics.pickupBranchSelected({ branch_name: branch.name });
                onChange({ pickupBranchId: id });
                clearError('pickupBranch');
              }}
              error={errors.pickupBranch}
              locale={locale}
              t={t}
            />
          </div>
        </Section>
      )}

      {/* 2 — Where it goes and who receives it. */}
      {isDelivery ? (
        <Section label={t('checkout.delivery_address')}>
          <div
            data-field="address"
            data-field-error={errors.address ? 'true' : undefined}
            className={highlightRing('address')}
          >
            <button
              type="button"
              onClick={() => openAddress('select')}
              className={`w-full text-start border rounded-sm px-3.5 py-3 flex items-center gap-3 transition-colors hover:border-primary/50 ${
                errors.address ? 'border-red-400' : 'border-gray-200'
              }`}
            >
              <Icon name={hasAddress ? 'location_on' : 'add_location_alt'} className="text-xl text-primary" />
              <span className="flex-1 min-w-0">
                {hasAddress ? (
                  <>
                    <span className="block font-body text-sm text-gray-800 truncate">
                      {formatAddress(currentDraft)}
                    </span>
                    <span className="block font-body text-xs text-gray-400 truncate mt-0.5">
                      {form.firstName} {form.lastName} · {form.phone}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="block font-body text-sm text-gray-800">{t('checkout.add_delivery_address')}</span>
                    <span className="block font-body text-xs text-gray-400 mt-0.5">{t('checkout.add_address_hint')}</span>
                  </>
                )}
              </span>
              <Icon name="chevron_right" className="text-lg text-gray-300" />
            </button>
            {errors.address && <p className="mt-1.5 text-xs text-red-500 font-body">{errors.address}</p>}
            {/* Said next to the thing that fixes it, and made a button rather
                than a sentence: the verification lives inside the address form,
                and "open the address form" is not something a customer should
                have to infer from an error message. Shown whenever it is
                outstanding — not only after the pay button has been pressed —
                so it is a step rather than a rejection. */}
            {verificationOutstanding && (
              <button
                type="button"
                data-field="verifyPhone"
                onClick={() => openAddress('verifyPhone')}
                className="mt-1.5 w-full text-start flex items-start gap-1.5 text-xs font-body text-amber-800"
              >
                <Icon name="verified_user" className="text-sm shrink-0" />
                <span className="underline underline-offset-2">
                  {t('checkout.verify_phone_required')}
                </span>
              </button>
            )}
            {/* The one line that answers "when", placed where the pin that
                decides it was just chosen. */}
            {arrival && !unserviceable && (
              // The same badge the product card and the PDP wear, so the answer
              // to "when" looks like one answer across the journey. It says more
              // here — a date and a time rather than a speed — because by this
              // point there is a pin and a real schedule behind it.
              <SpeedBadge className="mt-2.5">
                {t('checkout.estimated_delivery')}: {arrival}
              </SpeedBadge>
            )}
            {/* Directly under the thing that caused it, and above everything it
                makes pointless to fill in. */}
            {unserviceable && (
              <UnserviceableNotice
                onChangeAddress={() => openAddress('select')}
                onSwitchToPickup={() => {
                  analytics.selectDeliveryMethod({ method: 'pickup', fee: 0 });
                  onChange({ deliveryMethod: 'pickup' });
                  clearError('unserviceable');
                }}
                t={t}
              />
            )}
          </div>
        </Section>
      ) : (
        <Section label={t('checkout.contact_information')}>
          {/* Named as one field because it is one question. The button's
              "Enter contact info" points here, at both halves of it, rather
              than at whichever half happens to be empty. */}
          <div data-field="contact" className={`space-y-3 ${highlightRing('contact')}`}>
            <div className="grid grid-cols-2 gap-3">
              <div data-field="firstName" data-field-error={errors.firstName ? 'true' : undefined}>
                <Input
                  aria-label={t('common.first_name')}
                  placeholder={t('checkout.first_name_placeholder')}
                  value={form.firstName}
                  onChange={(e) => { onChange({ firstName: e.target.value }); clearError('firstName'); }}
                  error={errors.firstName}
                />
              </div>
              <Input
                aria-label={t('common.last_name')}
                placeholder={t('checkout.last_name_placeholder')}
                value={form.lastName}
                onChange={(e) => onChange({ lastName: e.target.value })}
              />
            </div>
            <div data-field="phone" data-field-error={errors.phone ? 'true' : undefined}>
              <PhoneInput
                value={form.phone}
                onChange={(v) => { onChange({ phone: v }); clearError('phone'); }}
                error={errors.phone}
              />
            </div>
          </div>
        </Section>
      )}

      {/* 3 — The last thing asked for, and no longer optional.
             `OrderCreate` requires it: an order with no address to send the
             receipt to is one the customer cannot track, cannot be told about
             when it is on its way, and cannot find again. So the "(optional)"
             label and the hint under the field are both gone — an affordance
             that says a field can be skipped, over a field that cannot, is
             worse than no affordance at all.
             Signed in, it is not a question: the account already has an
             address, and asking for it again invites a second one that no order
             history will match. */}
      <Section label={t('checkout.email')}>
        {accountEmail ? (
          <div className="flex items-start gap-2.5 border border-gray-200 bg-gray-50 rounded-sm px-3 py-2.5">
            <Icon name="mark_email_read" className="text-base text-primary mt-0.5" />
            <div className="min-w-0">
              <p className="font-body text-sm text-gray-800 truncate">{accountEmail}</p>
              <p className="font-body text-xs text-gray-400 mt-0.5">
                {t('checkout.email_signed_in_hint')}
              </p>
            </div>
          </div>
        ) : (
          <div
            data-field="email"
            data-field-error={errors.email ? 'true' : undefined}
            className={highlightRing('email')}
          >
            <Input
              type="email"
              // `required` rather than a marker in the label. No field on this
              // form wears an asterisk — the form asks for what it needs and
              // nothing else — so the way this one says it is mandatory is the
              // way the browser and a screen reader both already understand.
              required
              aria-required="true"
              aria-label={t('checkout.email')}
              placeholder={t('common.email_placeholder')}
              value={form.email}
              onChange={(e) => { onChange({ email: e.target.value }); clearError('email'); }}
              error={errors.email}
              helper={
                errors.email
                  ? undefined
                  : withFallback(
                      t,
                      'checkout.email_required_hint',
                      "We'll send your order confirmation and delivery updates here.",
                    )
              }
            />
          </div>
        )}
      </Section>

      {/* 4 — A choice when collecting, a statement when delivering: there is no
             cash handling on the delivery side. */}
      <Section label={t('checkout.payment_method')}>
        <div className="space-y-2">
          {paymentOptions.map((id) => {
            const isCod = id === 'cod';
            // Apple Pay, when offered, is a second card-family option, so the
            // card row stops being the sole answer and becomes a real choice.
            const only = paymentOptions.length === 1 && !applePayOffered;
            const row = (
              <>
                <Icon
                  name={isCod ? 'payments' : 'credit_card'}
                  className={`text-xl ${selectedPayment === id || only ? 'text-primary' : 'text-gray-400'}`}
                />
                <span className="flex-1 min-w-0">
                  <span className="block font-body text-sm text-gray-800">
                    {isCod ? t('checkout.cash_on_delivery') : t('checkout.credit_debit_card')}
                  </span>
                  <span className="block font-body text-xs text-gray-400 mt-0.5">
                    {isCod ? t('checkout.cod_pickup_sublabel') : t('checkout.payment_sublabel')}
                  </span>
                </span>
              </>
            );
            return only ? (
              // A statement, not an option — and dressed as the answer rather
              // than as the question. It used to be a grey, grey-bordered row:
              // pixel for pixel the *unselected* state of the choice rows a
              // customer meets one section above, so it read as "pick me" and
              // was tapped, and tapping it did nothing because there is nothing
              // to pick. Wearing the chosen state, with the tick that goes with
              // it, the row answers that question before it is asked.
              <div
                key={id}
                className="flex items-center gap-3 px-3.5 py-3 border border-primary rounded-sm bg-primary/5 cursor-default"
              >
                {row}
                <Icon name="check_circle" className="text-lg text-primary shrink-0" aria-hidden="true" />
                <span className="sr-only">
                  {withFallback(t, 'checkout.only_payment_method', 'Selected — the only payment method for this order')}
                </span>
              </div>
            ) : (
              <button
                key={id}
                type="button"
                aria-pressed={selectedPayment === id}
                onClick={() => {
                  analytics.paymentMethodSelected({
                    method: id,
                    delivery_method: form.deliveryMethod,
                    total,
                  });
                  // Choosing card or cash steps off Apple Pay; the underlying
                  // card/cash method is what the order is written with.
                  setApplePaySelected(false);
                  onChange({ paymentMethod: id });
                }}
                className={`w-full flex items-center gap-3 px-3.5 py-3 border rounded-sm text-start transition-colors ${
                  selectedPayment === id ? 'border-primary bg-primary/5' : 'border-gray-200 hover:border-primary/40'
                }`}
              >
                {row}
              </button>
            );
          })}

          {/* Apple Pay — the test-only extra option. Rendered only when the
              account is allowlisted, Stripe is the active gateway, and the
              device can actually do Apple Pay (`applePayOffered`). It is a
              card underneath, so the order is written no differently; what it
              changes is that the pay button below becomes the in-page sheet. */}
          {applePayOffered && (
            <button
              type="button"
              aria-pressed={selectedPayment === 'apple_pay'}
              onClick={() => {
                analytics.paymentMethodSelected({
                  method: 'apple_pay',
                  delivery_method: form.deliveryMethod,
                  total,
                });
                setApplePaySelected(true);
              }}
              className={`w-full flex items-center gap-3 px-3.5 py-3 border rounded-sm text-start transition-colors ${
                selectedPayment === 'apple_pay' ? 'border-primary bg-primary/5' : 'border-gray-200 hover:border-primary/40'
              }`}
            >
              <svg
                viewBox="0 0 24 24"
                aria-hidden="true"
                focusable="false"
                className={`w-5 h-5 shrink-0 ${selectedPayment === 'apple_pay' ? 'fill-primary' : 'fill-gray-400'}`}
              >
                <path d="M17.05 12.54c-.02-2.02 1.65-2.99 1.73-3.04-.94-1.38-2.41-1.57-2.93-1.59-1.25-.13-2.44.73-3.07.73-.63 0-1.61-.71-2.65-.69-1.36.02-2.62.79-3.32 2.01-1.42 2.46-.36 6.1 1.02 8.1.67.98 1.47 2.08 2.52 2.04 1.01-.04 1.39-.65 2.61-.65 1.22 0 1.56.65 2.63.63 1.09-.02 1.78-1 2.45-1.98.77-1.13 1.09-2.23 1.11-2.29-.02-.01-2.13-.82-2.15-3.25zM15.03 6.6c.56-.68.94-1.62.83-2.56-.81.03-1.79.54-2.37 1.21-.52.6-.97 1.56-.85 2.48.9.07 1.83-.46 2.39-1.13z" />
              </svg>
              <span className="flex-1 min-w-0">
                <span className="block font-body text-sm text-gray-800">
                  {withFallback(t, 'checkout.apple_pay_label', 'Apple Pay')}
                </span>
                <span className="block font-body text-xs text-gray-400 mt-0.5">
                  {withFallback(t, 'checkout.apple_pay_sublabel', 'Pay in one tap with Apple Pay')}
                </span>
              </span>
            </button>
          )}
        </div>
      </Section>

      {/* 5 — What they are buying, priced by the server. */}
      <Section label={t('checkout.order_summary')}>
        {/* Above the lines rather than below them: it is the reason the button
            will not submit, and a customer who has to scroll past their whole
            basket to find out why has already read the total twice. */}
        <UnavailableItems items={unavailableItems} cart={cart} t={t} />

        <OrderSummary
          cart={cart}
          retryOrder={retryOrder}
          preview={preview}
          lowOrderThreshold={deliveryRates?.low_order_threshold ?? 0}
          lowOrderFeeAmount={deliveryRates?.low_order_fee ?? 0}
          hasPin={hasPin}
          deliveryMethod={form.deliveryMethod}
          unserviceable={Boolean(unserviceable)}
          locale={locale}
          t={t}
        />

        {/* A promo code and a note matter to a few and are read by everyone, so
            they stay folded away until asked for. */}
        {!retryOrder && (
          <div className="mt-4">
            {showExtras ? (
              <div className="space-y-4 pt-1">
                {/* Named so a refusal that arrives from `POST /orders` can be
                    scrolled to once this fold-out has been opened for it. */}
                <div data-field="promo">
                  <PromoCodeStep
                    promoCode={form.promoCode}
                    promoDiscount={form.promoDiscount}
                    promoMessage={form.promoMessage}
                    subtotal={subtotal}
                    identity={promoIdentity}
                    serverError={promoRefusal}
                    onChange={(patch) => {
                      // Any edit to the code makes the last verdict about a
                      // different code.
                      setPromoRefusal(null);
                      onChange(patch);
                    }}
                  />
                </div>
                <textarea
                  value={form.notes}
                  onChange={(e) => onChange({ notes: e.target.value })}
                  placeholder={t('checkout.notes_placeholder')}
                  rows={2}
                  maxLength={500}
                  aria-label={t('checkout.order_notes_label')}
                  className="w-full px-3.5 py-2.5 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none resize-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
                />
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setShowExtras(true)}
                className="font-body text-xs text-primary hover:underline"
              >
                {t('checkout.add_promo_or_note')}
              </button>
            )}
          </div>
        )}
      </Section>

      {/* 6 — One button, and on a phone it never leaves the screen. */}
      <div className="hidden sm:block pt-2">
        {actionButton}
        <p className="mt-3 flex items-center justify-center gap-1.5 text-gray-400">
          <Icon name="lock" className="text-sm" />
          <span className="font-body text-xs">{t('checkout.security_note')}</span>
        </p>
      </div>

      {/* Sticky, not fixed.
          `position: fixed` resolves `bottom: 0` against the *layout* viewport,
          which on a phone is not the viewport you can see: browsers with a
          collapsing address bar keep the layout viewport at its tallest, so the
          bar settled hundreds of pixels above the visible bottom and appeared to
          float in the middle of the page while scrolling. A sticky element is
          laid out in normal flow and pinned by the scroller itself, so there is
          no second viewport for it to disagree with. `-mx-4` cancels the page
          gutter so it still spans edge to edge. */}
      <div className="sm:hidden sticky bottom-0 z-30 -mx-4 mt-4 bg-white/95 backdrop-blur border-t border-gray-100 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        {actionButton}
      </div>

      <AddressModal
        isOpen={addressOpen}
        onClose={() => setAddressOpen(false)}
        onSave={applyDraft}
        isAuthenticated={Boolean(user)}
        savedAddresses={savedAddresses}
        onSavedAddressesChange={setSavedAddresses}
        selectedAddressId={form.selectedAddressId}
        initialDraft={hasAddress ? currentDraft : null}
        // Offered only when this order actually needs it. A verification panel
        // on every address edit is an SMS asked of the large majority of
        // customers who have no coupon on the basket and nothing to prove.
        askToVerify={isDelivery && form.promoDiscount > 0 && form.promoNeedsVerify}
        verifiedPhone={verifiedPhone}
        onVerified={setVerifiedPhone}
        intent={addressIntent}
      />
    </div>
  );
}

export default function CheckoutPage() {
  return (
    <Suspense fallback={
      <div className="flex justify-center items-center py-20">
        <Spinner size="lg" />
      </div>
    }>
      <CheckoutContent />
    </Suspense>
  );
}
