'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { paymentsApi } from '@/lib/api-client';
import type { Order } from '@/lib/types';
import type { ApplePayHandlers } from './useApplePay';

/**
 * In-page Apple Pay for the storefront checkout — the half drawn by the card
 * gateway's own embedded SDK (Paymob's Pixel) rather than by Stripe.js.
 *
 * Same contract as `useApplePay` wherever it can be: offered only when the
 * server says this gateway's Apple Pay is live (`paymob_apple_pay` on the
 * eligibility check) *and* the device can do Apple Pay at all
 * (`ApplePaySession.canMakePayments()`); a card underneath, so the order is
 * written as a card order; settled by the gateway's webhook, never by anything
 * the browser says.
 *
 * One thing cannot be the same, and it is why this is two taps and not one.
 * Stripe lets the page open the sheet from its own button and write the order
 * once the sheet is authorised. The Pixel SDK does not: it draws its *own*
 * Apple Pay button (its `payFromOutside` event does not reach Apple Pay), and it
 * needs an intention's client secret before it can draw anything — so the
 * order, and the intention for it, must exist before the button does. `start()`
 * is the first tap: it writes the order, opens an Apple-Pay-only intention for
 * it, and mounts the SDK into the page. The SDK's button is the second tap, and
 * the sheet it opens is the payment.
 *
 * The customer never sees the gateway's name: its logo is switched off, and
 * everything this hook says is about Apple Pay.
 */

// ─── The SDK's global ────────────────────────────────────────────────────────

/**
 * What `afterPaymentComplete` is handed. Undocumented by the SDK; read from the
 * 1.2.7 bundle, where it is the raw HTTP response of the SDK's own pay call —
 * handed over for a decline as much as for a success, which is why it is read
 * below rather than taken as "paid". Every field optional because nothing
 * promises this shape.
 */
interface PixelPaymentResponse {
  status?: unknown;
  data?: unknown;
}

interface PixelOptions {
  publicKey: string;
  clientSecret: string;
  paymentMethods: string[];
  elementId: string;
  disablePay: boolean;
  showSaveCard: boolean;
  forceSaveCard: boolean;
  showPaymobLogo: boolean;
  customStyle?: Record<string, unknown>;
  beforePaymentComplete: (paymentMethod: unknown) => Promise<boolean>;
  afterPaymentComplete: (response: PixelPaymentResponse) => Promise<void> | void;
  onPaymentCancel: () => void;
}

interface PixelStatic {
  new (options: PixelOptions): unknown;
  /**
   * The SDK's registry of live instances, keyed by element id. Not part of its
   * documented surface — there is no documented way to take an instance down
   * at all — so it is read defensively, only to unmount the React root the SDK
   * created, and a future SDK without it degrades to "clear the element".
   */
  _instances?: Record<string, { _root?: { unmount?: () => void } | null } | undefined>;
}

declare global {
  interface Window {
    Pixel?: PixelStatic;
    /** Safari's Apple Pay JS entry point; absent on every browser that cannot do it. */
    ApplePaySession?: { canMakePayments?: () => boolean };
  }
}

/** Can this browser do Apple Pay at all? Safe on the server and anywhere it throws. */
function deviceCanApplePay(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.ApplePaySession?.canMakePayments?.() === true;
  } catch {
    // Safari throws from `canMakePayments` on an insecure origin, and a
    // browser extension shimming the global may throw anything. Either way
    // the answer is "no", not an error.
    return false;
  }
}

const UNAVAILABLE = 'Apple Pay is unavailable right now.';
const DECLINED = 'Your payment was not completed. Please try again.';

//: The SDK, loaded once per page however many times a customer presses.
//: Bundled from npm (`paymob-pixel`, pinned exactly) and imported only on the
//: press, client-side: it is a 2MB self-registering script that touches
//: `window` the moment it runs, so it must never be evaluated on the server
//: and is not worth shipping to a customer who pays any other way. A failed
//: load is not cached, so the next press can try again.
let pixelPromise: Promise<PixelStatic> | null = null;
function loadPixel(): Promise<PixelStatic> {
  if (typeof window === 'undefined') return Promise.reject(new Error(UNAVAILABLE));
  if (window.Pixel) return Promise.resolve(window.Pixel);
  if (!pixelPromise) {
    pixelPromise = import('paymob-pixel')
      .then(() => {
        if (!window.Pixel) throw new Error(UNAVAILABLE);
        return window.Pixel;
      })
      .catch((err: unknown) => {
        pixelPromise = null;
        throw err;
      });
  }
  return pixelPromise;
}

/**
 * The element the page renders for the SDK, once it is in the DOM.
 *
 * The page reveals the panel in the same press that calls `start()`, so the
 * element normally exists long before the order and the intention have come
 * back. Waited for rather than assumed, bounded, because a render that has not
 * committed yet is not a reason to fail a payment.
 */
async function waitForElement(id: string, attempts = 40): Promise<HTMLElement | null> {
  for (let i = 0; i < attempts; i++) {
    const el = document.getElementById(id);
    if (el) return el;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return null;
}

/**
 * What the sheet's completion means — read, not assumed.
 *
 * The SDK calls `afterPaymentComplete` after *every* attempt the customer
 * authorised, declined ones included, with its pay call's response. The rules
 * mirror the SDK's own reading of that response (1.2.7) so the page agrees with
 * what the sheet just showed:
 *
 *   - `data.success` true → paid. `data.pending` true → not decided yet; the
 *     webhook decides, so this goes to the confirmation like a success.
 *   - 400 "already paid / already being processed" → the money went through on
 *     an earlier attempt; also the confirmation.
 *   - 400 "retry limit reached" → declined, and this intention cannot be tried
 *     again: `final`, so the SDK is taken down and the next press opens a
 *     fresh one.
 *   - any other numeric status → declined; the customer may tap again.
 *   - no numeric status at all → a shape this code does not know. The sheet
 *     completed, and the webhook is the source of truth either way, so it is
 *     treated as done rather than shown as a failure that may not be one.
 */
type Outcome = { kind: 'paid' } | { kind: 'declined'; message: string; final: boolean };

function readOutcome(response: unknown): Outcome {
  const res = (response ?? {}) as PixelPaymentResponse;
  const status = typeof res.status === 'number' ? res.status : null;
  if (status === null) return { kind: 'paid' };

  const data = res.data;
  const body = (data && typeof data === 'object' && !Array.isArray(data) ? data : {}) as Record<
    string,
    unknown
  >;
  if (String(body.success) === 'true' || String(body.pending) === 'true') return { kind: 'paid' };

  const said =
    (typeof body.message === 'string' && body.message) ||
    (typeof body.msg === 'string' && body.msg) ||
    (Array.isArray(data) && typeof data[0] === 'string' && data[0]) ||
    '';

  if (status === 400) {
    if (said === 'Order has already been paid.' || said === 'Order already being processed for payment.') {
      return { kind: 'paid' };
    }
    if (said === 'Retry limit reached.') return { kind: 'declined', message: DECLINED, final: true };
  }
  return { kind: 'declined', message: said || DECLINED, final: false };
}

// ─── The hook ────────────────────────────────────────────────────────────────

/**
 * What one Apple Pay attempt needs from the page. The same contract as
 * `useApplePay`'s — including what an empty `onError` message means (the
 * customer dismissed the sheet) and what `stage` distinguishes — minus the
 * total: the SDK reads the amount off the server's intention, so there is no
 * figure for the page to hand it.
 */
export type PaymobApplePayHandlers = Omit<ApplePayHandlers, 'total'>;

/**
 * Where the flow is, for the panel: `idle` (nothing mounted), `preparing` (the
 * order and the intention are being written, the SDK loaded), `ready` (the
 * SDK's Apple Pay button is on the page, waiting for the second tap).
 */
export type PaymobApplePayStatus = 'idle' | 'preparing' | 'ready';

interface UsePaymobApplePayInput {
  /** Whether to probe at all. */
  enabled: boolean;
  /** The current order total, used only to route the server's eligibility check. */
  amount: number;
}

export function usePaymobApplePay({ enabled, amount }: UsePaymobApplePayInput) {
  const [available, setAvailable] = useState(false);
  const [status, setStatus] = useState<PaymobApplePayStatus>('idle');

  const probeStarted = useRef(false);
  const mountedRef = useRef(true);
  //: Bumped by every `start()` and `reset()`. An async step that finds it has
  //: moved on belongs to an attempt that was superseded, and does nothing.
  const generationRef = useRef(0);
  //: The generation of a `start()` between its press and its mount — a second
  //: press meanwhile is ignored rather than writing a second order.
  const busyRef = useRef<number | null>(null);
  //: The element the SDK is currently mounted in, so it can be taken down.
  const mountRef = useRef<{ host: HTMLElement; id: string } | null>(null);

  const teardown = useCallback(() => {
    const mounted = mountRef.current;
    mountRef.current = null;
    if (!mounted) return;
    try {
      const registry = window.Pixel?._instances;
      registry?.[mounted.id]?._root?.unmount?.();
      if (registry) delete registry[mounted.id];
    } catch {
      /* an SDK without the registry: clearing the element below is enough */
    }
    mounted.host.replaceChildren();
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      // Unmounted mid-flow: stop every pending step from reporting back, and
      // take the SDK's own React root down with the page.
      mountedRef.current = false;
      generationRef.current += 1;
      busyRef.current = null;
      teardown();
    };
  }, [teardown]);

  useEffect(() => {
    // Once, the first time there is a real total to ask with, and only on a
    // device that can do Apple Pay — so a browser that cannot never makes the
    // round-trip. The answer does not depend on the figure, so a total that
    // moves while the check is in flight does not cancel it (only unmounting
    // does): the preview landing a moment after the first paint must not be
    // the reason the option never appears.
    if (!enabled || probeStarted.current || amount <= 0) return;
    if (!deviceCanApplePay()) return;
    probeStarted.current = true;

    paymentsApi
      .applePayEligibility(amount)
      .then((eligibility) => {
        if (mountedRef.current && eligibility.paymob_apple_pay === true) setAvailable(true);
      })
      // The option simply stays hidden.
      .catch(() => {});
  }, [enabled, amount]);

  /**
   * The first tap: write the order, open the intention, mount the SDK's Apple
   * Pay button into `elementId`.
   *
   * The page must already be rendering an empty element with that id (or be
   * about to, in this same commit) — the SDK draws into it, and nothing else
   * may. Everything the SDK draws lives in a child element made here, so a
   * second attempt starts from a clean node rather than on top of the first.
   *
   * After it resolves the order is written and unpaid until the webhook says
   * otherwise. A dismissed sheet leaves it that way and the button where it
   * is, so the customer can simply tap again.
   */
  const start = useCallback(
    async (elementId: string, handlers: PaymobApplePayHandlers) => {
      if (busyRef.current !== null) return;
      const generation = ++generationRef.current;
      busyRef.current = generation;
      const live = () => mountedRef.current && generationRef.current === generation;

      teardown();
      setStatus('preparing');

      // Which half is running, for `onError` — the same distinction the card
      // path and the Stripe Apple Pay path report.
      let stage: 'create_order' | 'create_session' = 'create_order';
      let orderNumber: string | undefined;
      try {
        // The SDK does not depend on the order, so it loads while the order is
        // written; awaited only once there is something to mount.
        const pixelLoad = loadPixel();
        pixelLoad.catch(() => {}); // surfaced by the `await` below, not as unhandled

        const order: Order = await handlers.createOrder();
        stage = 'create_session';
        orderNumber = order.order_number;
        if (!live()) return;

        const session = await paymentsApi.createPaymobApplePaySession(order.order_number);
        if (!live()) return;

        const Pixel = await pixelLoad;
        if (!live()) return;

        const host = await waitForElement(elementId);
        if (!live()) return;
        if (!host) throw new Error(UNAVAILABLE);

        const mount = document.createElement('div');
        mount.id = `${elementId}-${generation}`;
        host.replaceChildren(mount);
        mountRef.current = { host, id: mount.id };

        //: Whether this attempt reached a settled payment, so nothing the SDK
        //: fires after it is read as a failure or a dismissal.
        let settled = false;

        new Pixel({
          publicKey: session.public_key,
          clientSecret: session.client_secret,
          paymentMethods: ['apple-pay'],
          elementId: mount.id,
          disablePay: false,
          showSaveCard: false,
          forceSaveCard: false,
          // The customer is paying Melting Moments with Apple Pay; the name of
          // the processor in between is not theirs to be shown.
          showPaymobLogo: false,
          customStyle: {
            Color_Container: 'transparent',
            Container_Padding: '0',
            Vertical_Padding: '0',
            Width_of_Container: '100%',
          },
          // A sheet opened after the page moved on (the panel closed, the
          // customer switched to card) is refused before it charges anything.
          beforePaymentComplete: async () => live(),
          afterPaymentComplete: async (response) => {
            if (!live() || settled) return;
            const outcome = readOutcome(response);
            if (outcome.kind === 'paid') {
              settled = true;
              handlers.onSuccess(order);
              return;
            }
            if (outcome.final) {
              // This intention is spent: take the button down so the next
              // press opens a fresh one instead of a button that cannot pay.
              generationRef.current += 1;
              teardown();
              setStatus('idle');
            }
            handlers.onError(outcome.message, 'create_session', order.order_number);
          },
          onPaymentCancel: () => {
            // A dismissal, not a failure: nothing is said, the order stays
            // unpaid, and the button stays for another go.
            if (live() && !settled) handlers.onError('');
          },
        });

        setStatus('ready');
      } catch (err) {
        if (!live()) return;
        generationRef.current += 1;
        teardown();
        setStatus('idle');
        handlers.onError(
          err instanceof Error && err.message ? err.message : 'Something went wrong taking your payment.',
          stage,
          orderNumber,
        );
      } finally {
        // Released only by the attempt that holds it — `reset()` releases it
        // itself, and a later attempt's flag is not this one's to clear.
        if (busyRef.current === generation) busyRef.current = null;
      }
    },
    [teardown],
  );

  /** Take the SDK down and forget the attempt — the customer chose another way to pay. */
  const reset = useCallback(() => {
    generationRef.current += 1;
    busyRef.current = null;
    teardown();
    setStatus('idle');
  }, [teardown]);

  return { available, status, start, reset };
}
