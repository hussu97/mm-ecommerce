/**
 * The two-tap Apple Pay path (`usePaymobApplePay`), driven at the boundary the
 * page relies on: what reaches `onError`/`onSuccess`, with which `stage`, and
 * what gets mounted where. The SDK is a fake that records its options, so a
 * test can fire the SDK's own callbacks — nothing here needs a real Apple Pay
 * sheet or the 2MB bundle.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Order } from '@/lib/types';

const mocks = vi.hoisted(() => ({
  applePayEligibility: vi.fn(),
  createPaymobApplePaySession: vi.fn(),
}));

vi.mock('@/lib/api-client', () => ({
  paymentsApi: {
    applePayEligibility: mocks.applePayEligibility,
    createPaymobApplePaySession: mocks.createPaymobApplePaySession,
  },
}));

// Never actually loaded — `window.Pixel` is set before every test, which is
// what `loadPixel` checks first — but mocked so a stray import cannot pull the
// real bundle into the test run.
vi.mock('paymob-pixel', () => ({}));

import { usePaymobApplePay, type PaymobApplePayHandlers } from './usePaymobApplePay';

interface FakeOptions {
  elementId: string;
  publicKey: string;
  clientSecret: string;
  paymentMethods: string[];
  showPaymobLogo: boolean;
  beforePaymentComplete: () => Promise<boolean>;
  afterPaymentComplete: (response: unknown) => Promise<void>;
  onPaymentCancel: () => void;
}

let constructed: FakeOptions[] = [];
const unmount = vi.fn();

function installFakePixel() {
  const FakePixel = function (this: unknown, options: FakeOptions) {
    constructed.push(options);
    FakePixel._instances[options.elementId] = { _root: { unmount } };
    document.getElementById(options.elementId)!.textContent = 'apple pay button';
  } as unknown as { new (o: FakeOptions): unknown; _instances: Record<string, unknown> };
  FakePixel._instances = {};
  (window as unknown as { Pixel: unknown }).Pixel = FakePixel;
}

function order(overrides: Partial<Order> = {}): Order {
  return { order_number: 'MM-1', email: 'a@b.com', ...overrides } as unknown as Order;
}

function handlers(overrides: Partial<PaymobApplePayHandlers> = {}): PaymobApplePayHandlers {
  return {
    createOrder: vi.fn(async () => order({ order_number: 'MM-42' })),
    onSuccess: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
}

const SESSION = {
  public_key: 'are_pk_test_1',
  client_secret: 'are_csk_test_1',
  amount: '100.00',
  currency: 'AED',
  order_number: 'MM-42',
};

beforeEach(() => {
  constructed = [];
  unmount.mockReset();
  document.body.innerHTML = '<div id="sdk"></div>';
  (window as unknown as { ApplePaySession: unknown }).ApplePaySession = {
    canMakePayments: () => true,
  };
  installFakePixel();
  mocks.applePayEligibility.mockReset().mockResolvedValue({ eligible: false, paymob_apple_pay: true });
  mocks.createPaymobApplePaySession.mockReset().mockResolvedValue(SESSION);
});

afterEach(() => {
  delete (window as unknown as { ApplePaySession?: unknown }).ApplePaySession;
});

describe('usePaymobApplePay — availability', () => {
  it('is available when the server says so and the device can do Apple Pay', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    await waitFor(() => expect(result.current.available).toBe(true));
    expect(mocks.applePayEligibility).toHaveBeenCalledWith(100);
  });

  it('is not available on a Stripe-only answer — `eligible` is Stripe’s', async () => {
    mocks.applePayEligibility.mockResolvedValue({ eligible: true });
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    await act(async () => {});
    expect(result.current.available).toBe(false);
  });

  it('never asks the server on a browser without Apple Pay', async () => {
    delete (window as unknown as { ApplePaySession?: unknown }).ApplePaySession;
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    await act(async () => {});
    expect(result.current.available).toBe(false);
    expect(mocks.applePayEligibility).not.toHaveBeenCalled();
  });

  it('treats a throwing canMakePayments as "no"', async () => {
    (window as unknown as { ApplePaySession: unknown }).ApplePaySession = {
      canMakePayments: () => {
        throw new Error('insecure origin');
      },
    };
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    await act(async () => {});
    expect(result.current.available).toBe(false);
  });

  it('survives the total moving while the check is in flight', async () => {
    let resolve!: (v: unknown) => void;
    mocks.applePayEligibility.mockReturnValue(new Promise((r) => (resolve = r)));
    const { result, rerender } = renderHook(
      ({ amount }) => usePaymobApplePay({ enabled: true, amount }),
      { initialProps: { amount: 100 } },
    );
    rerender({ amount: 125 });
    await act(async () => resolve({ eligible: false, paymob_apple_pay: true }));
    expect(result.current.available).toBe(true);
    expect(mocks.applePayEligibility).toHaveBeenCalledTimes(1);
  });
});

describe('usePaymobApplePay — the two taps', () => {
  it('writes the order, opens the intention, and mounts an Apple-Pay-only SDK', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const h = handlers();

    await act(() => result.current.start('sdk', h));

    expect(h.createOrder).toHaveBeenCalledTimes(1);
    expect(mocks.createPaymobApplePaySession).toHaveBeenCalledWith('MM-42');
    expect(constructed).toHaveLength(1);
    expect(constructed[0]).toMatchObject({
      publicKey: 'are_pk_test_1',
      clientSecret: 'are_csk_test_1',
      paymentMethods: ['apple-pay'],
      showPaymobLogo: false,
    });
    // Mounted into a child of the page's element, not the element itself.
    const host = document.getElementById('sdk')!;
    expect(host.children).toHaveLength(1);
    expect(host.firstElementChild!.id).toBe(constructed[0].elementId);
    expect(result.current.status).toBe('ready');
    expect(h.onError).not.toHaveBeenCalled();
  });

  it('goes to the confirmation when the sheet reports a payment', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const made = order({ order_number: 'MM-42' });
    const h = handlers({ createOrder: vi.fn(async () => made) });
    await act(() => result.current.start('sdk', h));

    await act(() => constructed[0].afterPaymentComplete({ status: 200, data: { success: true } }));

    expect(h.onSuccess).toHaveBeenCalledWith(made);
    expect(h.onError).not.toHaveBeenCalled();
  });

  it('treats a response it does not recognise as the sheet having completed', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const h = handlers();
    await act(() => result.current.start('sdk', h));

    await act(() => constructed[0].afterPaymentComplete(undefined));

    expect(h.onSuccess).toHaveBeenCalled();
  });

  it('reports a declined sheet as create_session and keeps the button for another go', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const h = handlers();
    await act(() => result.current.start('sdk', h));

    await act(() =>
      constructed[0].afterPaymentComplete({
        status: 200,
        data: { success: false, pending: false, message: 'Insufficient funds' },
      }),
    );

    expect(h.onError).toHaveBeenCalledWith('Insufficient funds', 'create_session', 'MM-42');
    expect(h.onSuccess).not.toHaveBeenCalled();
    expect(result.current.status).toBe('ready');
  });

  it('takes the button down once the intention has run out of retries', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const h = handlers();
    await act(() => result.current.start('sdk', h));

    await act(() =>
      constructed[0].afterPaymentComplete({ status: 400, data: { message: 'Retry limit reached.' } }),
    );

    expect(h.onError).toHaveBeenCalledWith(expect.any(String), 'create_session', 'MM-42');
    expect(result.current.status).toBe('idle');
    expect(unmount).toHaveBeenCalled();
    expect(document.getElementById('sdk')!.children).toHaveLength(0);
  });

  it('reports a dismissed sheet with an empty message and no stage', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const h = handlers();
    await act(() => result.current.start('sdk', h));

    act(() => constructed[0].onPaymentCancel());

    expect(h.onError).toHaveBeenCalledWith('');
    expect(result.current.status).toBe('ready');
  });

  it('reports create_order, with no order number, when writing the order fails', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const h = handlers({
      createOrder: vi.fn(async () => {
        throw new Error('Coupon has already been used');
      }),
    });

    await act(() => result.current.start('sdk', h));

    expect(h.onError).toHaveBeenCalledWith('Coupon has already been used', 'create_order', undefined);
    expect(mocks.createPaymobApplePaySession).not.toHaveBeenCalled();
    expect(result.current.status).toBe('idle');
  });

  it('reports create_session, with the order number, when the intention cannot be opened', async () => {
    mocks.createPaymobApplePaySession.mockRejectedValue(new Error('gateway unreachable'));
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const h = handlers();

    await act(() => result.current.start('sdk', h));

    expect(h.onError).toHaveBeenCalledWith('gateway unreachable', 'create_session', 'MM-42');
    expect(constructed).toHaveLength(0);
    expect(result.current.status).toBe('idle');
  });

  it('ignores a second press while the first is still writing the order', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    const h = handlers();

    await act(async () => {
      const first = result.current.start('sdk', h);
      const second = result.current.start('sdk', h);
      await Promise.all([first, second]);
    });

    expect(h.createOrder).toHaveBeenCalledTimes(1);
    expect(constructed).toHaveLength(1);
  });

  it('takes the previous SDK down before mounting a new one', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    await act(() => result.current.start('sdk', handlers()));
    await act(() => result.current.start('sdk', handlers()));

    expect(unmount).toHaveBeenCalledTimes(1);
    expect(constructed).toHaveLength(2);
    expect(constructed[0].elementId).not.toBe(constructed[1].elementId);
    expect(document.getElementById('sdk')!.children).toHaveLength(1);
  });

  it('says nothing, and mounts nothing, once the page has unmounted mid-flow', async () => {
    let resolveOrder!: (o: Order) => void;
    const h = handlers({ createOrder: vi.fn(() => new Promise<Order>((r) => (resolveOrder = r))) });
    const { result, unmount: unmountHook } = renderHook(() =>
      usePaymobApplePay({ enabled: true, amount: 100 }),
    );

    let pending!: Promise<void>;
    act(() => {
      pending = result.current.start('sdk', h);
    });
    unmountHook();
    await act(async () => {
      resolveOrder(order());
      await pending;
    });

    expect(mocks.createPaymobApplePaySession).not.toHaveBeenCalled();
    expect(constructed).toHaveLength(0);
    expect(h.onError).not.toHaveBeenCalled();
    expect(h.onSuccess).not.toHaveBeenCalled();
  });

  it('refuses a sheet opened after the customer stepped off Apple Pay', async () => {
    const { result } = renderHook(() => usePaymobApplePay({ enabled: true, amount: 100 }));
    await act(() => result.current.start('sdk', handlers()));
    const { beforePaymentComplete } = constructed[0];

    expect(await beforePaymentComplete()).toBe(true);
    act(() => result.current.reset());
    expect(await beforePaymentComplete()).toBe(false);
    expect(result.current.status).toBe('idle');
  });
});
