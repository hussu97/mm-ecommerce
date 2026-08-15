import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
      'track.total': 'Total',
      'order.status_confirmed': 'Confirmed',
      'order.store_pickup': 'Store Pickup',
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
      total: 265,
      // A guest sees the same fulfilment block as a signed-in customer; here
      // there is nothing to show yet, which has to render as an absence rather
      // than a crash.
      fulfilment: null,
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
      delivery_method: 'pickup',
    });
    // The status is translated now rather than title-cased from the raw enum,
    // which is what stopped an Arabic reader seeing the word "Confirmed".
    expect(await screen.findByText('Confirmed')).toBeInTheDocument();
    expect(screen.getByText('Store Pickup')).toBeInTheDocument();
    // formatPrice's canonical shape — amount first, code after (P3-13).
    expect(screen.getByText('265.00 AED')).toBeInTheDocument();
  });

  it('shows the estimate, the timeline and the live-tracking link once there is one', async () => {
    mocks.lookup.mockResolvedValue({
      order_number: 'MM-20260605-002',
      status: 'out_for_delivery',
      delivery_method: 'delivery',
      items_count: 2,
      created_at: '2026-06-05',
      total: 120,
      fulfilment: {
        method: 'delivery',
        stage: 'on_the_way',
        estimated_at: '2026-06-05T14:30:00+00:00',
        precision: 'time',
        tracking_url: 'https://share.lalamove.com/?SG1',
        courier_managed: true,
        packed_at: '2026-06-05T12:00:00+00:00',
        picked_up_at: '2026-06-05T13:45:00+00:00',
        delivered_at: null,
        branch: null,
      },
    });

    render(<TrackPage />);
    fireEvent.change(screen.getByLabelText('Order Number'), {
      target: { value: 'MM-20260605-002' },
    });
    fireEvent.change(screen.getByLabelText('Email Address'), {
      target: { value: 'guest@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Track Order' }));

    const live = await screen.findByRole('link', { name: /order.track_live/ });
    expect(live).toHaveAttribute('href', 'https://share.lalamove.com/?SG1');
    // On the shop's clock, not the test runner's — 14:30 UTC is 18:30 in Dubai.
    expect(screen.getByText(/6:30 PM/)).toBeInTheDocument();
  });

  it('degrades cleanly against an API that has not deployed yet', async () => {
    // `deploy-web` and `deploy-api` run in parallel, so for a minute or two
    // after a release this page is live against the previous API: no `total`,
    // no `fulfilment`. It has to render what it has rather than "AED NaN" and
    // an empty panel.
    mocks.lookup.mockResolvedValue({
      order_number: 'MM-20260605-004',
      status: 'confirmed',
      delivery_method: 'delivery',
      items_count: 3,
      created_at: '2026-06-05',
    });

    render(<TrackPage />);
    fireEvent.change(screen.getByLabelText('Order Number'), {
      target: { value: 'MM-20260605-004' },
    });
    fireEvent.change(screen.getByLabelText('Email Address'), {
      target: { value: 'guest@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Track Order' }));

    expect(await screen.findByText('MM-20260605-004')).toBeInTheDocument();
    expect(screen.getByText('Confirmed')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    expect(screen.queryByText('Total')).not.toBeInTheDocument();
  });

  it('shows the branch and its map link for a collection order', async () => {
    mocks.lookup.mockResolvedValue({
      order_number: 'MM-20260605-003',
      status: 'packed',
      delivery_method: 'pickup',
      items_count: 1,
      created_at: '2026-06-05',
      total: 80,
      fulfilment: {
        method: 'pickup',
        stage: 'ready',
        estimated_at: '2026-06-05T10:00:00+00:00',
        precision: 'exact',
        tracking_url: null,
        courier_managed: false,
        packed_at: null,
        picked_up_at: null,
        delivered_at: null,
        branch: {
          id: 'b1',
          reference: 'K001',
          name: 'Melting Moments Cakes',
          name_ar: null,
          address: 'Garden Tower 1, Al Majaz 3',
          address_ar: null,
          city: 'Sharjah',
          city_ar: null,
          phone: '06 552 1234',
          latitude: 25.33,
          longitude: 55.37,
          maps_url: 'https://maps.example/pin',
          opening_from: '09:00',
          opening_to: '23:00',
        },
      },
    });

    render(<TrackPage />);
    fireEvent.change(screen.getByLabelText('Order Number'), {
      target: { value: 'MM-20260605-003' },
    });
    fireEvent.change(screen.getByLabelText('Email Address'), {
      target: { value: 'guest@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Track Order' }));

    expect(await screen.findByText('Melting Moments Cakes')).toBeInTheDocument();
    expect(screen.getByText('Garden Tower 1, Al Majaz 3')).toBeInTheDocument();
    expect(screen.getByText('Sharjah')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /order.open_in_maps/ })).toHaveAttribute(
      'href',
      'https://maps.example/pin',
    );
    // No courier, so nothing to watch and no promise of a link to come.
    expect(screen.queryByText('order.track_live_pending')).not.toBeInTheDocument();
  });
});
