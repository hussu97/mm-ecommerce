/**
 * A custom order is opened on the one order page. The old custom-order detail
 * route only redirects there, and the custom-orders list is one untabbed list
 * whose every row and chip links there.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const nav = vi.hoisted(() => ({
  redirect: vi.fn(),
  replace: vi.fn(),
  search: '',
}));

vi.mock('next/navigation', () => ({
  redirect: nav.redirect,
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => '/custom-orders',
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const api = vi.hoisted(() => ({
  list: vi.fn(),
  status: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  customOrdersApi: { list: api.list, status: api.status },
}));

import CustomOrderRedirect from '@/app/(dashboard)/custom-orders/[orderNumber]/page';
import CustomOrdersPage from '@/app/(dashboard)/custom-orders/page';

const ROW = {
  id: '11111111-1111-1111-1111-111111111111',
  order_number: 'MM-C-0001',
  status: 'created',
  delivery_date: '2026-09-30',
  delivery_time: null,
  customer_name: 'Sara',
  customer_phone: null,
  summary: 'Wedding cake',
  total: '500.00',
  delivery_provider: null,
  kitchen_printed_at: null,
  created_at: '2026-09-26T10:00:00Z',
};

describe('custom-order routing', () => {
  beforeEach(() => {
    nav.redirect.mockReset();
    nav.search = '';
    api.list.mockReset();
    api.status.mockReset();
    api.list.mockResolvedValue({ items: [ROW], total: 1, page: 1, per_page: 50, pages: 1 });
    api.status.mockResolvedValue({ enabled: true, branch_name: 'Custom Kitchen' });
  });

  it('redirects the old custom-order page to the order page', async () => {
    await CustomOrderRedirect({ params: Promise.resolve({ orderNumber: 'MM-C-0001' }) });
    expect(nav.redirect).toHaveBeenCalledWith('/orders/MM-C-0001');
  });

  it('lists every custom order in one table, with no status tabs, linking to the order page', async () => {
    render(<CustomOrdersPage />);
    await waitFor(() => expect(document.querySelector('table')).not.toBeNull());
    const table = within(document.querySelector('table') as HTMLElement);
    expect((await table.findByText('Wedding cake')).closest('a')).toHaveAttribute('href', '/orders/MM-C-0001');

    // One request for all of them: no status group narrows it.
    await waitFor(() => expect(api.list).toHaveBeenCalled());
    for (const [params] of api.list.mock.calls) {
      expect(params).not.toHaveProperty('status_group');
    }
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByRole('button', { name: /^pending$/i })).toBeNull();

    // The status is a column instead.
    expect(table.getByText('Status')).toBeInTheDocument();
    expect(table.getByText('Taken')).toBeInTheDocument();
  });

  it('links calendar chips to the order page too', async () => {
    nav.search = 'view=calendar&month=2026-09';
    render(<CustomOrdersPage />);
    const chips = await screen.findAllByTitle(/MM-C-0001/);
    for (const chip of chips) expect(chip).toHaveAttribute('href', '/orders/MM-C-0001');
  });
});
