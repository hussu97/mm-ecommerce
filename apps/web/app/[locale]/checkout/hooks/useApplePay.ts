'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  loadStripe,
  type PaymentRequest,
  type PaymentRequestPaymentMethodEvent,
  type Stripe,
} from '@stripe/stripe-js';

import { paymentsApi } from '@/lib/api';
import type { Order } from '@/lib/types';

/**
 * In-page Apple Pay for the storefront checkout — the Stripe half of it.
 *
 * The checkout stays provider-agnostic everywhere else: a card is a card and
 * which processor settles it is the server's business. Apple Pay is the one
 * exception, because the sheet is a Stripe surface drawn by Stripe.js against a
 * PaymentIntent — so this hook exists and offers itself only when three things
 * are all true: Stripe is the active card gateway (the server's eligibility
 * check), the device can actually do Apple Pay (`canMakePayment().applePay`),
 * and the publishable key is present. Miss any one and `available` stays false
 * and the option never renders — which is exactly the required behaviour on a
 * browser that cannot do Apple Pay.
 *
 * The money path is the ordinary one. `pay()` shows the sheet, and on
 * authorisation creates the order and a PaymentIntent and confirms it in-page;
 * the intent carries the order number, so `payment_intent.succeeded` confirms
 * the order through the same webhook every card payment already uses.
 */

const PUBLISHABLE_KEY = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY;

//: Shared across the app — `loadStripe` injects one script tag and is memoised
//: so a second checkout mount does not add a second.
let stripePromise: Promise<Stripe | null> | null = null;
function getStripe(): Promise<Stripe | null> {
  if (!PUBLISHABLE_KEY) return Promise.resolve(null);
  if (!stripePromise) stripePromise = loadStripe(PUBLISHABLE_KEY);
  return stripePromise;
}

/** What a single Apple Pay press needs from the page, read fresh at press time. */
export interface ApplePayHandlers {
  /** The figure to charge, in AED. Kept current so the sheet shows the real total. */
  total: number;
  /** Write the order the form describes and hand back what was created. */
  createOrder: () => Promise<Order>;
  /** The payment went through: go to the confirmation for this order. */
  onSuccess: (order: Order) => void;
  /** Something was refused or failed. Empty message = the customer dismissed the sheet. */
  onError: (message: string) => void;
}

interface UseApplePayInput {
  /** Whether to attempt Apple Pay at all — the page turns it off for a returned unpaid order. */
  enabled: boolean;
  /** The current order total, used to probe eligibility and the device. */
  amount: number;
}

export function useApplePay({ enabled, amount }: UseApplePayInput) {
  const [available, setAvailable] = useState(false);

  const stripeRef = useRef<Stripe | null>(null);
  const requestRef = useRef<PaymentRequest | null>(null);
  //: The current press's callbacks. The `paymentmethod` listener is attached
  //: once to a reused PaymentRequest, so it reads the live handlers from here
  //: rather than closing over stale ones.
  const handlersRef = useRef<ApplePayHandlers | null>(null);
  //: Whether this press reached a settled payment, so a trailing `cancel`
  //: (Stripe fires one after the sheet closes) is not read as an abandonment.
  const settledRef = useRef(false);
  const setupStarted = useRef(false);

  const minorAmount = useCallback((value: number) => Math.round(value * 100), []);

  useEffect(() => {
    // Set up once, the first time there is a real total to probe with. The
    // device's Apple Pay capability does not change with the amount, so there
    // is nothing to redo when the total moves — `pay()` updates the figure on
    // the sheet itself.
    if (!enabled || setupStarted.current || amount <= 0 || !PUBLISHABLE_KEY) return;
    setupStarted.current = true;

    let cancelled = false;
    (async () => {
      let eligibility;
      try {
        eligibility = await paymentsApi.applePayEligibility(amount);
      } catch {
        return; // the option simply stays hidden
      }
      if (cancelled || !eligibility.eligible) return;

      const stripe = await getStripe();
      if (cancelled || !stripe) return;

      const request = stripe.paymentRequest({
        country: 'AE',
        currency: 'aed',
        total: { label: 'Melting Moments Cakes', amount: minorAmount(amount) },
        requestPayerName: false,
        requestPayerEmail: false,
        requestPayerPhone: false,
      });

      let result;
      try {
        result = await request.canMakePayment();
      } catch {
        return;
      }
      // Apple Pay specifically — a device that can only do Google Pay is not
      // what this feature offers, and the task is Apple Pay only.
      if (cancelled || !result || !result.applePay) return;

      request.on('paymentmethod', async (ev: PaymentRequestPaymentMethodEvent) => {
        const handlers = handlersRef.current;
        if (!handlers) {
          ev.complete('fail');
          return;
        }
        settledRef.current = false;
        try {
          // The order is written now, after the sheet is authorised, because
          // `show()` had to be called synchronously on the press to count as a
          // user gesture — there was no room to create it first.
          const order = await handlers.createOrder();
          const intent = await paymentsApi.createApplePayIntent(order.order_number);

          const { error, paymentIntent } = await stripe.confirmCardPayment(
            intent.client_secret,
            { payment_method: ev.paymentMethod.id },
            { handleActions: false },
          );

          if (error) {
            // Close the sheet on the failure so the customer is not left staring
            // at a spinner, then say what happened on the page behind it.
            ev.complete('fail');
            handlers.onError(error.message ?? 'Payment failed. Please try again.');
            return;
          }

          ev.complete('success');

          // A card that needs 3-D Secure: the first confirm returned without
          // charging and asked for a step-up. Now that the sheet is closed we
          // can run it, which pops the bank's own dialog.
          if (paymentIntent && paymentIntent.status === 'requires_action') {
            const stepUp = await stripe.confirmCardPayment(intent.client_secret);
            if (stepUp.error) {
              handlers.onError(stepUp.error.message ?? 'Payment could not be authorised.');
              return;
            }
          }

          settledRef.current = true;
          handlers.onSuccess(order);
        } catch (err) {
          ev.complete('fail');
          handlers.onError(
            err instanceof Error ? err.message : 'Something went wrong taking your payment.',
          );
        }
      });

      request.on('cancel', () => {
        // Stripe fires `cancel` both when the customer dismisses the sheet and,
        // on some browsers, right after a successful payment closes it. Only the
        // first is an abandonment.
        if (!settledRef.current) handlersRef.current?.onError('');
      });

      stripeRef.current = stripe;
      requestRef.current = request;
      if (!cancelled) setAvailable(true);
    })();

    return () => {
      cancelled = true;
    };
  }, [enabled, amount, minorAmount]);

  /**
   * Open the Apple Pay sheet for one payment.
   *
   * Must be called straight from the button's click handler with no awaited
   * work in between — Safari only opens the sheet in response to a genuine user
   * gesture, and an intervening `await` forfeits it. So this is synchronous:
   * it records the press's callbacks, updates the figure on the sheet, and
   * shows it. Everything asynchronous — writing the order, charging the card —
   * happens afterwards, inside the `paymentmethod` handler.
   */
  const pay = useCallback(
    (handlers: ApplePayHandlers) => {
      const request = requestRef.current;
      if (!request) {
        handlers.onError('Apple Pay is unavailable right now.');
        return;
      }
      handlersRef.current = handlers;
      settledRef.current = false;
      request.update({
        total: { label: 'Melting Moments Cakes', amount: minorAmount(handlers.total) },
      });
      request.show();
    },
    [minorAmount],
  );

  return { available, pay };
}
