/**
 * F-WEB-11: Apple Pay failures used to emit no analytics at all, and
 * `checkoutStepComplete` fired the instant the button was pressed — before
 * the sheet had even opened, let alone been authorised. `page.tsx`'s
 * `handleApplePay` is where the actual `analytics.*` calls now live (matching
 * `handleSubmit`'s card/COD path), which means this hook's job is to report
 * *which half broke* — `stage` — and the order number once one exists, so the
 * page has something to report with. These tests drive the hook directly and
 * assert what reaches `onError`/`onSuccess`, since that boundary is where the
 * bug actually lived: nothing here needs a real Apple Pay sheet.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Order } from '@/lib/types';

const mocks = vi.hoisted(() => ({
  applePayEligibility: vi.fn(),
  createApplePayIntent: vi.fn(),
  canMakePayment: vi.fn(),
  confirmCardPayment: vi.fn(),
}));

// `useApplePay.ts` reads `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` once, at module
// load, into a top-level constant — so it has to be set before the static
// import below runs, not before a test does. `vi.hoisted` is what makes that
// possible: Vitest moves it (and `vi.mock`, below) above every import in this
// file, in the order they appear.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY = 'pk_test_123';
});

vi.mock('@/lib/api', () => ({
  paymentsApi: {
    applePayEligibility: mocks.applePayEligibility,
    createApplePayIntent: mocks.createApplePayIntent,
  },
}));

/** Handlers the hook registers via `request.on(event, cb)`, captured so the
 * test can fire them directly instead of driving a real Apple Pay sheet. */
let registered: Record<string, (...args: unknown[]) => unknown> = {};
/** The most recent PaymentRequest the hook built, for asserting on the sheet. */
let lastRequest: ReturnType<typeof makePaymentRequest> | null = null;

function makePaymentRequest() {
  return {
    canMakePayment: mocks.canMakePayment,
    on: vi.fn((event: string, cb: (...args: unknown[]) => unknown) => {
      registered[event] = cb;
    }),
    update: vi.fn(),
    show: vi.fn(),
  };
}

vi.mock('@stripe/stripe-js', () => ({
  loadStripe: vi.fn(async () => ({
    paymentRequest: vi.fn(() => (lastRequest = makePaymentRequest())),
    confirmCardPayment: mocks.confirmCardPayment,
  })),
}));

import { useApplePay, type ApplePayHandlers } from './useApplePay';

function order(overrides: Partial<Order> = {}): Order {
  return { order_number: 'MM-1', email: 'a@b.com', ...overrides } as unknown as Order;
}

/** Renders the hook and waits for Apple Pay to report itself available. */
async function renderAvailable(amount = 100) {
  const view = renderHook(() => useApplePay({ enabled: true, amount }));
  await waitFor(() => expect(view.result.current.available).toBe(true));
  return view;
}

beforeEach(() => {
  registered = {};
  mocks.applePayEligibility.mockReset().mockResolvedValue({ eligible: true });
  mocks.canMakePayment.mockReset().mockResolvedValue({ applePay: true });
  mocks.createApplePayIntent.mockReset();
  mocks.confirmCardPayment.mockReset();
});

describe('useApplePay — stage-aware failure reporting', () => {
  it('reports create_order, with no order number, when writing the order fails', async () => {
    const { result } = await renderAvailable();
    const onError = vi.fn();
    const handlers: ApplePayHandlers = {
      total: 100,
      createOrder: vi.fn(async () => { throw new Error('Coupon has already been used'); }),
      onSuccess: vi.fn(),
      onError,
    };

    act(() => result.current.pay(handlers));
    await act(async () => {
      await registered.paymentmethod({ paymentMethod: { id: 'pm_1' }, complete: vi.fn() });
    });

    expect(onError).toHaveBeenCalledWith('Coupon has already been used', 'create_order', undefined);
  });

  it('reports create_session, with the order number, when the intent cannot be created', async () => {
    const { result } = await renderAvailable();
    mocks.createApplePayIntent.mockRejectedValue(new Error('gateway unreachable'));
    const onError = vi.fn();
    const handlers: ApplePayHandlers = {
      total: 100,
      createOrder: vi.fn(async () => order({ order_number: 'MM-42' })),
      onSuccess: vi.fn(),
      onError,
    };

    act(() => result.current.pay(handlers));
    await act(async () => {
      await registered.paymentmethod({ paymentMethod: { id: 'pm_1' }, complete: vi.fn() });
    });

    expect(onError).toHaveBeenCalledWith('gateway unreachable', 'create_session', 'MM-42');
  });

  it('reports create_session when Stripe declines the card', async () => {
    const { result } = await renderAvailable();
    mocks.createApplePayIntent.mockResolvedValue({ client_secret: 'secret_1' });
    mocks.confirmCardPayment.mockResolvedValue({ error: { message: 'Your card was declined.' } });
    const onError = vi.fn();
    const complete = vi.fn();
    const handlers: ApplePayHandlers = {
      total: 100,
      createOrder: vi.fn(async () => order({ order_number: 'MM-42' })),
      onSuccess: vi.fn(),
      onError,
    };

    act(() => result.current.pay(handlers));
    await act(async () => {
      await registered.paymentmethod({ paymentMethod: { id: 'pm_1' }, complete });
    });

    expect(onError).toHaveBeenCalledWith('Your card was declined.', 'create_session', 'MM-42');
    expect(complete).toHaveBeenCalledWith('fail');
  });

  it('reports create_session when a 3-D Secure step-up fails', async () => {
    const { result } = await renderAvailable();
    mocks.createApplePayIntent.mockResolvedValue({ client_secret: 'secret_1' });
    mocks.confirmCardPayment
      .mockResolvedValueOnce({ paymentIntent: { status: 'requires_action' } })
      .mockResolvedValueOnce({ error: { message: 'Authentication failed.' } });
    const onError = vi.fn();
    const handlers: ApplePayHandlers = {
      total: 100,
      createOrder: vi.fn(async () => order({ order_number: 'MM-42' })),
      onSuccess: vi.fn(),
      onError,
    };

    act(() => result.current.pay(handlers));
    await act(async () => {
      await registered.paymentmethod({ paymentMethod: { id: 'pm_1' }, complete: vi.fn() });
    });

    expect(onError).toHaveBeenCalledWith('Authentication failed.', 'create_session', 'MM-42');
  });

  it('calls onSuccess, not onError, once the payment actually settles', async () => {
    const { result } = await renderAvailable();
    mocks.createApplePayIntent.mockResolvedValue({ client_secret: 'secret_1' });
    mocks.confirmCardPayment.mockResolvedValue({ paymentIntent: { status: 'succeeded' } });
    const onError = vi.fn();
    const onSuccess = vi.fn();
    const madeOrder = order({ order_number: 'MM-42' });
    const handlers: ApplePayHandlers = {
      total: 100,
      createOrder: vi.fn(async () => madeOrder),
      onSuccess,
      onError,
    };

    act(() => result.current.pay(handlers));
    await act(async () => {
      await registered.paymentmethod({ paymentMethod: { id: 'pm_1' }, complete: vi.fn() });
    });

    expect(onSuccess).toHaveBeenCalledWith(madeOrder);
    expect(onError).not.toHaveBeenCalled();
  });

  it('reports the sheet being dismissed with no stage, distinct from a real failure', async () => {
    const { result } = await renderAvailable();
    const onError = vi.fn();
    const handlers: ApplePayHandlers = {
      total: 100,
      createOrder: vi.fn(async () => order()),
      onSuccess: vi.fn(),
      onError,
    };

    act(() => result.current.pay(handlers));
    act(() => {
      registered.cancel();
    });

    expect(onError).toHaveBeenCalledWith('');
    expect(onError).not.toHaveBeenCalledWith(expect.anything(), 'create_order', expect.anything());
    expect(onError).not.toHaveBeenCalledWith(expect.anything(), 'create_session', expect.anything());
  });
});

describe('useApplePay — setup survives the total moving', () => {
  /** An eligibility answer the test releases by hand, so the total can change
   * while setup is still waiting on it. */
  function deferredEligibility() {
    let release!: (value: { eligible: boolean }) => void;
    mocks.applePayEligibility.mockImplementation(
      () => new Promise((resolve) => { release = resolve; }),
    );
    return (value = { eligible: true }) => release(value);
  }

  it('still becomes available when the amount changes mid-setup', async () => {
    // A delivery checkout: the total moves the moment the delivery-fee preview
    // lands, typically while eligibility is still in flight. That used to
    // cancel the setup for good and Apple Pay never appeared.
    const release = deferredEligibility();
    const view = renderHook(
      ({ amount }) => useApplePay({ enabled: true, amount }),
      { initialProps: { amount: 100 } },
    );
    view.rerender({ amount: 115 });
    await act(async () => { release(); });

    await waitFor(() => expect(view.result.current.available).toBe(true));
    // The total moving is not a reason to probe again.
    expect(mocks.applePayEligibility).toHaveBeenCalledTimes(1);
  });

  it('retries a setup that was torn down before it finished', async () => {
    const release = deferredEligibility();
    const view = renderHook(
      ({ enabled }) => useApplePay({ enabled, amount: 100 }),
      { initialProps: { enabled: true } },
    );
    view.rerender({ enabled: false });
    await act(async () => { release(); });
    expect(view.result.current.available).toBe(false);

    mocks.applePayEligibility.mockReset().mockResolvedValue({ eligible: true });
    view.rerender({ enabled: true });
    await waitFor(() => expect(view.result.current.available).toBe(true));
  });

  it('shows the live total on the sheet, not the one setup probed with', async () => {
    const release = deferredEligibility();
    const view = renderHook(
      ({ amount }) => useApplePay({ enabled: true, amount }),
      { initialProps: { amount: 100 } },
    );
    view.rerender({ amount: 115 });
    await act(async () => { release(); });
    await waitFor(() => expect(view.result.current.available).toBe(true));

    const handlers: ApplePayHandlers = {
      total: 115,
      createOrder: vi.fn(),
      onSuccess: vi.fn(),
      onError: vi.fn(),
    };
    act(() => view.result.current.pay(handlers));
    expect(lastRequest?.update).toHaveBeenCalledWith({
      total: { label: 'Melting Moments Cakes', amount: 11500 },
    });
  });
});
