/**
 * The always-on half of P0-4.
 *
 * A promo applied on the basket page rides `sessionStorage` into the checkout
 * form, and the pay button quotes its number — but the re-check that kept that
 * number honest used to live inside a component mounted only after the "add
 * promo or note" fold-out was opened. Change the basket in another tab, or
 * apply a capped percentage discount and then grow the subtotal, and the
 * button quoted a total the server would refuse to charge. These tests pin the
 * two things that fix it: one shared validator that both surfaces use (so the
 * cart and the checkout cannot drift on what they send or record), and a
 * re-validation hook whose life is tied to the page, not to a fold-out.
 */

import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  validate: vi.fn(),
  promoApplied: vi.fn(),
  promoFailed: vi.fn(),
}));

vi.mock('./api', () => ({
  promoApi: { validate: mocks.validate },
}));

// `failureReason` is kept real: what the hook records for a failed request is
// part of what these tests are pinning.
vi.mock('./analytics', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./analytics')>();
  return {
    ...actual,
    analytics: { promoApplied: mocks.promoApplied, promoFailed: mocks.promoFailed },
  };
});

import {
  appliedPromoCode,
  promoOutcomeOf,
  useCartPromoRecovery,
  usePromoRevalidation,
  usePromoValidation,
  type PromoOutcome,
} from './use-promo-validation';

describe('promoOutcomeOf', () => {
  it('maps a refusal to invalid, carrying the server’s own reason', () => {
    expect(promoOutcomeOf('SPENT', { valid: false, discount_amount: 0, message: 'This code has expired' }))
      .toEqual({ kind: 'invalid', code: 'SPENT', message: 'This code has expired' });
  });

  it('keeps a reasonless refusal null rather than inventing wording', () => {
    const outcome = promoOutcomeOf('SPENT', { valid: false, discount_amount: 0, message: null });
    expect(outcome).toEqual({ kind: 'invalid', code: 'SPENT', message: null });
  });

  it('maps an acceptance to applied, with the discount as a number', () => {
    // The API serialises decimals as strings on some paths; the form does
    // arithmetic with this, so it must come out numeric.
    const outcome = promoOutcomeOf('WELCOME15', {
      valid: true,
      discount_amount: '15.00' as unknown as number,
      message: 'AED 15 off',
    });
    expect(outcome).toEqual({
      kind: 'applied', code: 'WELCOME15', discount: 15, message: 'AED 15 off', needsVerify: false,
    });
  });

  it('reports verification as owed only when the gate applies and is unmet', () => {
    const gated = (phone_verified?: boolean) => promoOutcomeOf('WELCOME15', {
      valid: true, discount_amount: 15, message: null,
      requires_phone_verification: true, phone_verified,
    });
    expect(gated(false)).toMatchObject({ kind: 'applied', needsVerify: true });
    expect(gated(true)).toMatchObject({ kind: 'applied', needsVerify: false });
    // An older API build that does not send the field must not read as
    // "outstanding" and block a checkout — see `pendingVerification`.
    expect(gated(undefined)).toMatchObject({ kind: 'applied', needsVerify: false });
  });
});

describe('appliedPromoCode', () => {
  it('watches the code only while a discount is actually on', () => {
    expect(appliedPromoCode('WELCOME15', 15)).toBe('WELCOME15');
    // Typed but not applied: an empty box must never talk to the server.
    expect(appliedPromoCode('WELCOME15', 0)).toBe('');
    expect(appliedPromoCode('', 15)).toBe('');
    expect(appliedPromoCode('   ', 15)).toBe('');
  });

  it('normalises the way every validator does', () => {
    expect(appliedPromoCode('  welcome15 ', 15)).toBe('WELCOME15');
  });
});

describe('usePromoValidation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.validate.mockResolvedValue({ valid: true, discount_amount: 15, message: null });
  });

  it('never talks to the server for an empty code', async () => {
    const { result } = renderHook(() => usePromoValidation('cart'));
    expect(await result.current.validateCode('   ', { subtotal: 100 })).toBeNull();
    expect(mocks.validate).not.toHaveBeenCalled();
  });

  it('sends whatever identity the surface knows, unchanged', async () => {
    const { result } = renderHook(() => usePromoValidation('checkout'));
    await result.current.validateCode('welcome15', {
      subtotal: 100,
      identity: { email: 'a@b.com', phone: '+971501234567', delivery_method: 'delivery' },
    });
    expect(mocks.validate).toHaveBeenCalledWith('WELCOME15', 100, {
      email: 'a@b.com', phone: '+971501234567', delivery_method: 'delivery',
    });
  });

  it('records an announced application, under the surface that made it', async () => {
    const { result } = renderHook(() => usePromoValidation('cart'));
    await result.current.validateCode('WELCOME15', { subtotal: 100, fromTray: true });
    expect(mocks.promoApplied).toHaveBeenCalledWith({
      code: 'WELCOME15', discount: 15, surface: 'cart', subtotal: 100, from_tray: true,
    });
  });

  it('does not count a silent re-check as a fresh redemption', async () => {
    const { result } = renderHook(() => usePromoValidation('checkout'));
    const outcome = await result.current.validateCode('WELCOME15', { subtotal: 100, announced: false });
    expect(outcome).toMatchObject({ kind: 'applied', discount: 15 });
    expect(mocks.promoApplied).not.toHaveBeenCalled();
  });

  it('records a refusal even when nobody asked — a dead coupon is a fact', async () => {
    mocks.validate.mockResolvedValue({ valid: false, discount_amount: 0, message: 'This code has expired' });
    const { result } = renderHook(() => usePromoValidation('checkout'));
    const outcome = await result.current.validateCode('WELCOME15', { subtotal: 100, announced: false });
    expect(outcome).toEqual({ kind: 'invalid', code: 'WELCOME15', message: 'This code has expired' });
    expect(mocks.promoFailed).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'WELCOME15', reason: 'This code has expired', surface: 'checkout' }),
    );
  });

  it('falls back to the caller’s reason when the server gives none', async () => {
    mocks.validate.mockResolvedValue({ valid: false, discount_amount: 0, message: null });
    const { result } = renderHook(() => usePromoValidation('cart'));
    await result.current.validateCode('WELCOME15', { subtotal: 100, fallbackReason: 'invalid' });
    expect(mocks.promoFailed).toHaveBeenCalledWith(expect.objectContaining({ reason: 'invalid' }));
  });

  it('keeps a network failure distinct from a refusal', async () => {
    mocks.validate.mockRejectedValue(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => usePromoValidation('cart'));
    const outcome = await result.current.validateCode('WELCOME15', { subtotal: 100 });
    // `error`, not `invalid`: the server never said no, so the caller must not
    // take the discount off. The server re-validates at order creation anyway.
    expect(outcome).toEqual({ kind: 'error', code: 'WELCOME15' });
    expect(mocks.promoFailed).toHaveBeenCalledWith(expect.objectContaining({ reason: 'network_error' }));
  });

  it('does not record a failed silent re-check at all', async () => {
    mocks.validate.mockRejectedValue(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => usePromoValidation('checkout'));
    const outcome = await result.current.validateCode('WELCOME15', { subtotal: 100, announced: false });
    expect(outcome).toEqual({ kind: 'error', code: 'WELCOME15' });
    expect(mocks.promoFailed).not.toHaveBeenCalled();
  });
});

describe('usePromoRevalidation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.validate.mockResolvedValue({ valid: true, discount_amount: 15, message: null });
  });

  type Props = Parameters<typeof usePromoRevalidation>[0];
  const renderRevalidation = (over: Partial<Props> = {}) => {
    const onResult = vi.fn();
    const initial: Props = {
      code: 'WELCOME15',
      discount: 15,
      subtotal: 100,
      identity: { email: null, phone: null },
      onResult,
      ...over,
    };
    const view = renderHook((props: Props) => usePromoRevalidation(props), { initialProps: initial });
    return { ...view, onResult, initial };
  };

  it('re-checks a promo that arrives already applied — the fold-out no longer decides', async () => {
    // The sessionStorage handover from the basket page: the discount is on the
    // form from the first render, and nothing else ever changes. This is
    // exactly the promo the old code never re-checked unless the customer
    // happened to open the "add promo or note" panel.
    renderRevalidation();
    await waitFor(
      () => expect(mocks.validate).toHaveBeenCalledWith('WELCOME15', 100, { email: null, phone: null }),
      { timeout: 2000 },
    );
    expect(mocks.promoApplied).not.toHaveBeenCalled();
  });

  it('re-checks when the identity changes under an applied code', async () => {
    const { rerender, initial } = renderRevalidation();
    await waitFor(() => expect(mocks.validate).toHaveBeenCalledTimes(1), { timeout: 2000 });

    rerender({ ...initial, identity: { email: null, phone: '+971509999999' } });
    await waitFor(
      () => expect(mocks.validate).toHaveBeenCalledWith('WELCOME15', 100, { email: null, phone: '+971509999999' }),
      { timeout: 2000 },
    );
  });

  it('hands an invalid answer up so the discount can come off the pay button', async () => {
    mocks.validate.mockResolvedValue({ valid: false, discount_amount: 0, message: 'This code is for new customers only' });
    const { onResult } = renderRevalidation();
    await waitFor(
      () => expect(onResult).toHaveBeenCalledWith({
        kind: 'invalid', code: 'WELCOME15', message: 'This code is for new customers only',
      } satisfies PromoOutcome),
      { timeout: 2000 },
    );
  });

  it('never talks to the server while nothing is applied', async () => {
    renderRevalidation({ code: 'WELCOME15', discount: 0 });
    await new Promise((resolve) => setTimeout(resolve, 700));
    expect(mocks.validate).not.toHaveBeenCalled();
  });

  it('stays quiet while a returned unpaid order owns the totals', async () => {
    renderRevalidation({ enabled: false });
    await new Promise((resolve) => setTimeout(resolve, 700));
    expect(mocks.validate).not.toHaveBeenCalled();
  });
});


describe('useCartPromoRecovery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.validate.mockResolvedValue({ valid: true, discount_amount: 5.25, message: null });
  });

  const recover = (props: Partial<Parameters<typeof useCartPromoRecovery>[0]> = {}) => {
    const onRecovered = vi.fn();
    const view = renderHook(
      (p: { cartCode: string | null; formCode: string }) =>
        useCartPromoRecovery({
          cartCode: p.cartCode,
          formCode: p.formCode,
          subtotal: 35,
          onRecovered,
          ...props,
        }),
      { initialProps: { cartCode: 'NEW', formCode: '', ...props } as never },
    );
    return { onRecovered, ...view };
  };

  it('puts the basket’s code back when nothing else carried it', async () => {
    // The bug: `sessionStorage` failed, so the checkout had no code, priced the
    // order at full price and would have submitted it that way.
    const { onRecovered } = recover();

    await waitFor(() => expect(onRecovered).toHaveBeenCalledTimes(1));
    expect(onRecovered).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'applied', code: 'NEW', discount: 5.25 }),
    );
  });

  it('leaves a code the customer already has alone', async () => {
    // Restored from the session, or typed into the panel. Either way it is
    // theirs and the basket does not get to overwrite it.
    const { onRecovered } = recover({ formCode: 'TYPED' } as never);

    await new Promise((r) => setTimeout(r, 20));
    expect(mocks.validate).not.toHaveBeenCalled();
    expect(onRecovered).not.toHaveBeenCalled();
  });

  it('does nothing when the basket has no code', async () => {
    const { onRecovered } = recover({ cartCode: null } as never);

    await new Promise((r) => setTimeout(r, 20));
    expect(mocks.validate).not.toHaveBeenCalled();
    expect(onRecovered).not.toHaveBeenCalled();
  });

  it('asks once, however often the page re-renders', async () => {
    // `onRecovered` changes the form, which re-renders, which re-runs the
    // effect. Without the guard that is a validation request per render.
    const { onRecovered, rerender } = recover();
    await waitFor(() => expect(onRecovered).toHaveBeenCalledTimes(1));

    rerender({ cartCode: 'NEW', formCode: '' });
    rerender({ cartCode: 'NEW', formCode: '' });

    await new Promise((r) => setTimeout(r, 20));
    expect(mocks.validate).toHaveBeenCalledTimes(1);
  });

  it('drops a refused code in silence', async () => {
    // The customer never typed it on this screen. A toast about a coupon they
    // did not enter is noise, and the basket already told them if it failed
    // there.
    mocks.validate.mockResolvedValue({ valid: false, discount_amount: 0, message: 'Expired' });
    const { onRecovered } = recover();

    await new Promise((r) => setTimeout(r, 20));
    expect(mocks.validate).toHaveBeenCalled();
    expect(onRecovered).not.toHaveBeenCalled();
  });

  it('loses the race to a code that arrives while it was asking', async () => {
    // The session restore is an effect too. A validation started while the
    // form was empty must not land afterwards and replace the code the restore
    // brought — which is why the check is repeated inside the callback.
    let release: (v: unknown) => void = () => {};
    mocks.validate.mockReturnValue(new Promise((r) => { release = r; }));

    const { onRecovered, rerender } = recover();
    rerender({ cartCode: 'NEW', formCode: 'RESTORED' });
    release({ valid: true, discount_amount: 5.25, message: null });

    await new Promise((r) => setTimeout(r, 20));
    expect(onRecovered).not.toHaveBeenCalled();
  });

  it('stays quiet until the basket is loaded', async () => {
    const { onRecovered } = recover({ enabled: false } as never);

    await new Promise((r) => setTimeout(r, 20));
    expect(mocks.validate).not.toHaveBeenCalled();
    expect(onRecovered).not.toHaveBeenCalled();
  });
});
