import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  search: '',
  lookup: vi.fn(),
  orderTracked: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {
    constructor(_status: number, message: string) {
      super(message);
    }
  },
  trackApi: {
    lookup: mocks.lookup,
  },
}));

vi.mock('@/lib/analytics', () => ({
  analytics: {
    orderTracked: mocks.orderTracked,
  },
}));

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    t: (key: string) => ({
      'track.title': 'Track Your Order',
      'track.subtitle': 'Enter your order number and email to check the status.',
      'track.validation_error': 'Please enter your order number and email.',
      'track.generic_error': 'Something went wrong. Please try again.',
      'track.order_number': 'Order Number',
      'track.email_address': 'Email Address',
      'track.track_button': 'Track Order',
      'track.order_label': 'Order',
      'track.status': 'Status',
      'track.delivery': 'Delivery',
      'track.items': 'Items',
      'track.placed': 'Placed',
      'common.email_placeholder': 'you@example.com',
    }[key] ?? key),
  }),
}));

import TrackPage from './page';

describe('TrackPage', () => {
  beforeEach(() => {
    mocks.search = '';
    mocks.lookup.mockReset();
    mocks.orderTracked.mockReset();
    window.history.replaceState(null, '', '/en/track');
  });

  it('prefills and looks up an order from email deeplink params', async () => {
    mocks.search = 'order_number=MM-20260605-001&email=guest%40example.com';
    window.history.replaceState(null, '', `/en/track?${mocks.search}`);
    mocks.lookup.mockResolvedValue({
      order_number: 'MM-20260605-001',
      status: 'confirmed',
      delivery_method: 'pickup',
      items_count: 1,
      created_at: '2026-06-05',
    });

    render(<TrackPage />);

    expect(screen.getByLabelText('Order Number')).toHaveValue('MM-20260605-001');
    expect(screen.getByLabelText('Email Address')).toHaveValue('guest@example.com');

    await waitFor(() => {
      expect(mocks.lookup).toHaveBeenCalledWith(
        'MM-20260605-001',
        'guest@example.com',
      );
    });
    expect(mocks.orderTracked).toHaveBeenCalledWith({
      order_number: 'MM-20260605-001',
      status: 'confirmed',
    });
    expect(await screen.findByText('Confirmed')).toBeInTheDocument();
  });
});
