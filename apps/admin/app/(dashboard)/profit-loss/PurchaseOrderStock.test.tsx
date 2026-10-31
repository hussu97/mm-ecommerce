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

function mount(over: Partial<Parameters<typeof PurchaseOrderStock>[0]> = {}) {
  return render(
    <PurchaseOrderStock
      dateFrom="2026-10-01"
      dateTo="2026-10-31"
      branchIds={[]}
      entityIds={[]}
      channelFiltered={false}
      {...over}
    />,
  );
}

describe('PurchaseOrderStock', () => {
  beforeEach(() => {
    apiMocks.purchaseOrders.mockReset();
    apiMocks.purchaseOrders.mockResolvedValue(REPORT);
  });

  it('renders the API’s figures, consumed taken off, without deriving any', async () => {
    mount({ branchIds: ['b1'] });
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
    await waitFor(() =>
      expect(screen.getByText(/No purchase order with stock lines/)).toBeInTheDocument(),
    );
  });
});
