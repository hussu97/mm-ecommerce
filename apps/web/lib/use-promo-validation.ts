'use client';

/**
 * One implementation of "put this code on this basket".
 *
 * The cart page and the checkout both used to carry their own copy of this —
 * both calling `promoApi.validate`, both working out `pendingVerification`,
 * both firing `promo_applied`/`promo_failed` — and the copies had already
 * drifted: they sent different identity payloads and would have kept drifting.
 * A coupon is judged on the account, the email *and* the phone, and two
 * validators that disagree about what to send are two screens that disagree
 * about whether the discount is real. So the call, the outcome and the
 * analytics live here once; what stays at each surface is its own copy
 * (translation keys, toasts, where an error renders) and how much identity it
 * actually knows — the basket has only an account email, the checkout has the
 * whole form.
 */

import { useCallback, useEffect, useRef } from 'react';
import { promoApi } from './api';
import { pendingVerification, type PromoValidateResponse } from './types';
import { analytics, failureReason, type Surface } from './analytics';

/**
 * Who the server should judge the coupon against, as far as the caller knows.
 *
 * Not optional decoration — see `promoApi.validate`. A new-customer coupon is
 * refused on an account, an email or a phone that has ordered before, and
 * order creation checks all three. Send whatever the surface knows; the server
 * decides.
 */
export interface PromoIdentity {
  email?: string | null;
  phone?: string | null;
  /** Which kind of order this is. The phone gate only applies to deliveries. */
  delivery_method?: 'delivery' | 'pickup' | null;
}

/**
 * What one validation round said, reduced to the three answers a surface can
 * act on. `applied` carries everything the form needs in one piece — the
 * discount and whether a verified number is still owed are one fact about one
 * coupon, and handed over separately they can be set in different orders and
 * disagree.
 */
export type PromoOutcome =
  | { kind: 'applied'; code: string; discount: number; message: string; needsVerify: boolean }
  /** The server said no. `message` is its reason, null when it gave none. */
  | { kind: 'invalid'; code: string; message: string | null }
  /**
   * The request itself failed — network, timeout, 500. Deliberately distinct
   * from `invalid`: a code the server refused must come off the basket, but a
   * code the server never heard about must not. The server re-validates at
   * order creation regardless, and dropping a legitimate discount because one
   * request timed out is the worse of the two failures.
   */
  | { kind: 'error'; code: string };

/**
 * Fold a validate response into an outcome. Pure, so the mapping — including
 * the `pendingVerification` computation every screen has to agree on — is
 * testable without a component or a network.
 */
export function promoOutcomeOf(code: string, result: PromoValidateResponse): PromoOutcome {
  if (!result.valid) {
    return { kind: 'invalid', code, message: result.message };
  }
  return {
    kind: 'applied',
    code,
    discount: Number(result.discount_amount),
    message: result.message ?? '',
    needsVerify: pendingVerification(result),
  };
}

/**
 * The code the always-on re-check should be watching, or '' for none.
 *
 * One definition of "is a promo actually applied", because this is the
 * decision that used to live only inside a component mounted behind a fold-out
 * toggle — and a decision nobody renders is a decision nobody makes. A code
 * that is typed but not applied (discount 0) is not watched, so an empty box
 * never talks to the server.
 */
export function appliedPromoCode(code: string, discount: number): string {
  const trimmed = code.trim().toUpperCase();
  return discount > 0 && trimmed ? trimmed : '';
}

interface ValidateOptions {
  subtotal: number;
  identity?: PromoIdentity;
  /**
   * False for automatic re-checks. Those run whenever the basket or the
   * identity moves — every added cake, every finished phone number — so
   * counting each as a fresh redemption in `promo_applied`, or as a fresh
   * network failure in `promo_failed`, would drown the real ones. A refusal is
   * recorded either way: a coupon that stopped being valid is a fact about the
   * coupon, not about who noticed.
   */
  announced?: boolean;
  /** Set when the coupon tray applied it, so the tray's rate can be read. */
  fromTray?: boolean;
  /**
   * What to record in `promo_failed.reason` when the server refuses without
   * saying why. Per-surface because each surface has its own wording for that
   * gap; the server's own words win whenever there are any.
   */
  fallbackReason?: string;
}

/**
 * The validator itself. `surface` names where the attempt happened in the
 * analytics — the funnel reads cart applications and checkout applications as
 * different steps.
 */
export function usePromoValidation(surface: Surface) {
  const validateCode = useCallback(
    async (raw: string, opts: ValidateOptions): Promise<PromoOutcome | null> => {
      const code = raw.trim().toUpperCase();
      if (!code) return null;
      const { subtotal, identity, announced = true, fromTray, fallbackReason = 'invalid' } = opts;
      const tray = fromTray === undefined ? {} : { from_tray: fromTray };
      try {
        const result = await promoApi.validate(code, subtotal, identity);
        const outcome = promoOutcomeOf(code, result);
        if (outcome.kind === 'applied') {
          if (announced) {
            analytics.promoApplied({ code, discount: outcome.discount, surface, subtotal, ...tray });
          }
        } else {
          analytics.promoFailed({
            code,
            // The server's own words, kept short enough to group on. Whether a
            // coupon was refused for being expired, spent or not-for-you is
            // the difference between a campaign that ended and one that is
            // broken.
            reason: (result.message ?? fallbackReason).slice(0, 60),
            surface,
            subtotal,
            ...tray,
          });
        }
        return outcome;
      } catch (err) {
        if (announced) {
          analytics.promoFailed({ code, reason: failureReason(err), surface, subtotal, ...tray });
        }
        return { kind: 'error', code };
      }
    },
    [surface],
  );

  return { validateCode };
}

interface RevalidationOptions {
  code: string;
  discount: number;
  subtotal: number;
  identity?: PromoIdentity;
  /** False while a returned unpaid order owns the totals — its promo is priced already. */
  enabled?: boolean;
  fallbackReason?: string;
  onResult: (outcome: PromoOutcome) => void;
}

/**
 * Keep an applied discount equal to what the order will actually be written
 * with.
 *
 * An applied discount is priced against a subtotal and judged against an
 * identity, and both move while the customer is still filling the form in —
 * they add a cake in another tab, they type their phone. This re-check used to
 * live inside `PromoCodeStep`, which is mounted only after the "add promo or
 * note" fold-out is opened — so a promo carried over from the basket was never
 * re-checked at all unless the customer happened to open a panel most never
 * open, while the pay button quoted its number anyway. Hoisted here so the
 * check runs for as long as the page does, fold-out or no fold-out.
 *
 * Keyed on the values themselves rather than on a timer, debounced because
 * half of them are fields being typed into. The applied code is a dependency
 * too — that is what makes a promo restored from `sessionStorage` get checked
 * on arrival even when nothing else moves. The price of that is one redundant
 * silent re-check shortly after a manual apply, which is a no-op by
 * construction: same code, same subtotal, same identity, same answer.
 */
export function usePromoRevalidation({
  code,
  discount,
  subtotal,
  identity,
  enabled = true,
  fallbackReason,
  onResult,
}: RevalidationOptions) {
  const { validateCode } = usePromoValidation('checkout');

  // The newest callback, readable from a stale closure. The re-check must fire
  // on basket and identity changes only — re-firing because a parent re-render
  // produced a new `onResult` identity would turn every keystroke anywhere on
  // the form into a validation request.
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  const applied = appliedPromoCode(code, discount);
  useEffect(() => {
    if (!enabled || !applied) return;
    const timer = setTimeout(() => {
      void validateCode(applied, {
        subtotal,
        identity,
        announced: false,
        fallbackReason,
      }).then((outcome) => {
        if (outcome) onResultRef.current(outcome);
      });
    }, 500);
    return () => clearTimeout(timer);
    // `identity` is watched field-by-field: it is rebuilt as an object on every
    // render, and as a whole it would re-fire on identity (the reference kind)
    // rather than identity (the customer kind). `fallbackReason` is a
    // translation and deliberately not a trigger — new wording is not a reason
    // to talk to the server.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, applied, subtotal, identity?.email, identity?.phone, identity?.delivery_method, validateCode]);
}
