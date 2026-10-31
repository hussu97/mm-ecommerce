import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({ purchaseOrders: vi.fn() }));

vi.mock('@/lib/api', () => ({
  profitLossApi: { purchaseOrders: apiMocks.purchaseOrders },
}));

import { PurchaseOrderStock } from './PurchaseOrderStock';

const REPORT = {
  date_from: '2026-10-01',
  date_to: '2026-10-31',
  received: 270,
  consumed: 70,
  net: 200,
  used_elsewhere: 30,
  in_stock: 170,
  purchase_orders: [
    {
      purchase_order_id: 'po-2',
      reference: 'PO-000002',
      business_date: '2026-10-12',
      supplier: 'Bakers Village',
      branch: 'Sharjah Kitchen',
      received: 70,
      consumed: 20,
      net: 50,
      used_elsewhere: 0,
      in_stock: 50,
    },
    {
      purchase_order_id: 'po-1',
      reference: 'PO-000001',
      business_date: '2026-10-03',
      supplier: 'Modern General Trading',
      branch: 'Sharjah Kitchen',
      received: 200,
      consumed: 50,
      net: 150,
      used_elsewhere: 30,
      in_stock: 120,
    },
  ],
};

type Props = Parameters<typeof PurchaseOrderStock>[0];
const PROPS: Props = {
  dateFrom: '2026-10-01',
  dateTo: '2026-10-31',
  branchIds: [],
  entityIds: [],
  channelFiltered: false,
};

function mount(over: Partial<Props> = {}) {
  return render(<PurchaseOrderStock {...PROPS} {...over} />);
}

const toggle = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const expand = () => toggle('Show purchase-order stock');

describe('PurchaseOrderStock', () => {
  beforeEach(() => {
    apiMocks.purchaseOrders.mockReset();
    apiMocks.purchaseOrders.mockResolvedValue(REPORT);
  });

  it('renders the API’s figures, consumed taken off, without deriving any', async () => {
    mount({ branchIds: ['b1'] });
    expand();
    await screen.findByText('Received (2 purchase orders)');
    expect(apiMocks.purchaseOrders).toHaveBeenCalledWith({
      date_from: '2026-10-01',
      date_to: '2026-10-31',
      branch_ids: ['b1'],
      legal_entity_ids: undefined,
    });
    const row = (label: string) => screen.getByText(label).closest('div')!.textContent!;
    expect(row('Received (2 purchase orders)')).toMatch(/270/);
    expect(row('Consumed in P&L orders, to date')).toMatch(/−.*70/);
    expect(row('Net · in no COGS')).toMatch(/200/);
    expect(row('Still in stock, raw or in goods not sold yet')).toMatch(/170/);
    expect(row('Used elsewhere · count shortfalls, internal use, unsold orders')).toMatch(/30/);
    // The per-PO table opens on demand, each reference linking to its PO.
    expect(screen.queryByText('PO-000001')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show the 2 purchase orders' }));
    expect(screen.getByText('PO-000001').closest('a')).toHaveAttribute('href', '/purchase-orders/po-1');
  });

  it('says the channel filter does not apply when one is set', async () => {
    mount({ channelFiltered: true });
    await screen.findByText(/channel filter does not apply here/);
  });

  it('says so when the window closed no purchase order with stock', async () => {
    apiMocks.purchaseOrders.mockResolvedValue({ ...REPORT, purchase_orders: [] });
    mount();
    expand();
    await waitFor(() =>
      expect(screen.getByText(/No purchase order with stock lines/)).toBeInTheDocument(),
    );
  });

  it('does not run the trace until the section is opened', async () => {
    const { rerender } = mount();
    expect(screen.getByRole('button', { name: 'Show purchase-order stock' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    // A filter change while collapsed does not fetch either.
    rerender(<PurchaseOrderStock {...PROPS} dateFrom="2026-09-01" />);
    expect(apiMocks.purchaseOrders).not.toHaveBeenCalled();
    expect(screen.queryByText(/Received \(/)).toBeNull();

    expand();
    await screen.findByText('Received (2 purchase orders)');
    expect(apiMocks.purchaseOrders).toHaveBeenCalledTimes(1);
    expect(apiMocks.purchaseOrders).toHaveBeenCalledWith(
      expect.objectContaining({ date_from: '2026-09-01' }),
    );
  });

  it('refetches on a filter change while open, and not on a reopen with the same filters', async () => {
    const { rerender } = mount();
    expand();
    await screen.findByText('Received (2 purchase orders)');

    rerender(<PurchaseOrderStock {...PROPS} branchIds={['b2']} />);
    await waitFor(() => expect(apiMocks.purchaseOrders).toHaveBeenCalledTimes(2));
    expect(apiMocks.purchaseOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ branch_ids: ['b2'] }),
    );
    await waitFor(() => expect(screen.getByText('Received (2 purchase orders)')).toBeInTheDocument());

    toggle('Hide purchase-order stock');
    expect(screen.queryByText('Received (2 purchase orders)')).toBeNull();
    expand();
    expect(screen.getByText('Received (2 purchase orders)')).toBeInTheDocument();
    expect(apiMocks.purchaseOrders).toHaveBeenCalledTimes(2);

    // Collapsed, the filters move on; reopening fetches for the new ones.
    toggle('Hide purchase-order stock');
    rerender(<PurchaseOrderStock {...PROPS} branchIds={['b3']} />);
    expect(apiMocks.purchaseOrders).toHaveBeenCalledTimes(2);
    expand();
    await waitFor(() => expect(apiMocks.purchaseOrders).toHaveBeenCalledTimes(3));
    expect(apiMocks.purchaseOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ branch_ids: ['b3'] }),
    );
  });
});
