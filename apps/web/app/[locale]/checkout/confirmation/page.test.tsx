import { render, screen, waitFor } from '@testing-library/react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  orderCompleted: vi.fn(),
}));

let mockSearch = new URLSearchParams();
vi.mock('next/navigation', () => ({
  useSearchParams: () => mockSearch,
}));

// happy-dom may not fully implement all localStorage methods (see
// lib/api.test.ts); provide a reliable mock so the dedupe under test has
// somewhere real to read and write.
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    key: (index: number) => Object.keys(store)[index] ?? null,
    get length() { return Object.keys(store).length; },
  };
})();

beforeAll(() => {
  vi.stubGlobal('localStorage', localStorageMock);
});

vi.mock('@/lib/api', () => ({
  ordersApi: {
    get: mocks.get,
  },
}));

vi.mock('@/lib/analytics', () => ({
  analytics: {
    orderCompleted: mocks.orderCompleted,
  },
}));

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    locale: 'en',
    t: (key: string) => key,
  }),
}));

vi.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: null }),
}));

// Not what this test is about — the create-account pitch has its own Firebase
// and phone-verification dependencies that would otherwise need mocking too.
vi.mock('./CreateAccountNudge', () => ({
  CreateAccountNudge: () => null,
}));

import ConfirmationPage from './page';

function fakeOrder(orderNumber: string) {
  return {
    id: 'o1',
    order_number: orderNumber,
    user_id: null,
    email: 'guest@example.com',
    delivery_method: 'pickup',
    delivery_fee: 0,
    low_order_fee: 0,
    subtotal: 100,
    discount_amount: 0,
    total: 100,
    vat_rate: 0.05,
    vat_amount: 5,
    total_excl_vat: 95,
    status: 'confirmed',
    promo_code_used: null,
    shipping_address_snapshot: null,
    payment_method: 'card',
    payment_provider: 'stripe',
    payment_id: 'pay_1',
    notes: null,
    cancellation_reason: null,
    created_at: '2026-06-05T00:00:00Z',
    updated_at: '2026-06-05T00:00:00Z',
    items: [
      {
        id: 'i1',
        product_id: 'p1',
        product_name: 'Salted Caramel Brownie',
        product_sku: 'SKU-1',
        product_translations: {},
        quantity: 1,
        base_price: 100,
        options_price: 0,
        unit_price: 100,
        total_price: 100,
        selected_options_snapshot: [],
      },
    ],
    email_has_account: false,
  };
}

describe('ConfirmationPage', () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.orderCompleted.mockReset();
    window.localStorage.clear();
    mockSearch = new URLSearchParams({ order_number: 'MM-20260605-001' });
  });

  it('fires order_completed once for the first view of an order', async () => {
    mocks.get.mockResolvedValue(fakeOrder('MM-20260605-001'));

    render(<ConfirmationPage />);

    await waitFor(() => {
      expect(mocks.orderCompleted).toHaveBeenCalledTimes(1);
    });
    expect(mocks.orderCompleted).toHaveBeenCalledWith(
      expect.objectContaining({ order_number: 'MM-20260605-001' }),
    );
    expect(await screen.findByText('MM-20260605-001')).toBeInTheDocument();
  });

  it('does not re-fire order_completed on a second render of the same order (refresh/back)', async () => {
    mocks.get.mockResolvedValue(fakeOrder('MM-20260605-001'));

    const { unmount } = render(<ConfirmationPage />);
    await waitFor(() => expect(mocks.orderCompleted).toHaveBeenCalledTimes(1));
    unmount();

    // Simulates a refresh or a back-button return to the same confirmation
    // URL: a brand new mount, same order number, same localStorage.
    render(<ConfirmationPage />);
    await screen.findByText('MM-20260605-001');

    expect(mocks.orderCompleted).toHaveBeenCalledTimes(1);
  });

  it('still fires for a different order number', async () => {
    mocks.get.mockResolvedValueOnce(fakeOrder('MM-20260605-001'));
    const { unmount } = render(<ConfirmationPage />);
    await waitFor(() => expect(mocks.orderCompleted).toHaveBeenCalledTimes(1));
    unmount();

    mockSearch = new URLSearchParams({ order_number: 'MM-20260605-002' });
    mocks.get.mockResolvedValueOnce(fakeOrder('MM-20260605-002'));
    render(<ConfirmationPage />);

    await waitFor(() => expect(mocks.orderCompleted).toHaveBeenCalledTimes(2));
  });
});
