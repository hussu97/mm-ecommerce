import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  itemCostLayers: vi.fn(),
  itemCostHistory: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {},
}));

vi.mock('@/lib/pos-api', () => ({
  inventoryApi: {
    itemCostLayers: apiMocks.itemCostLayers,
    itemCostHistory: apiMocks.itemCostHistory,
  },
}));

import { CostBreakdownModal } from './CostBreakdownModal';
import type { Branch, InventoryItem } from '@/lib/pos-types';

const COUNTS = { purchasing: 3, quantity_adjustment: 2, consumption_from_production: 7 };

function row(line: string, type: string, quantity: string) {
  return {
    line_id: line,
    transaction_id: `t-${line}`,
    reference: line.toUpperCase(),
    type,
    posted_at: '2026-09-28T10:00:00Z',
    business_date: '2026-09-28',
    quantity,
    unit_cost: '0.0252',
    total_cost: '302.4000',
    booked_total_cost: null,
    is_provisional: false,
    superseded: false,
    purchase_order_id: null,
    purchase_order_reference: null,
    cost_source_reference: null,
    cost_source_purchase_order_id: null,
    running_quantity: '13405',
    running_value: '348.7650',
    running_average_cost: '0.026',
  };
}

function history(items: ReturnType<typeof row>[]) {
  return {
    item_id: 'item-1',
    branch_id: 'b1',
    items,
    total: items.length,
    page: 1,
    per_page: 50,
    pages: 1,
    type_counts: COUNTS,
  };
}

async function openHistory() {
  render(
    <CostBreakdownModal
      item={{ id: 'item-1', name: 'Whipping Cream' } as InventoryItem}
      branches={[{ id: 'b1', name: 'Sharjah Kitchen' } as Branch]}
      onClose={() => {}}
    />,
  );
  fireEvent.click(screen.getByText('Costing history'));
  await screen.findByText('PO-1');
}

describe('costing history type filter', () => {
  beforeEach(() => {
    apiMocks.itemCostLayers.mockReset().mockReturnValue(new Promise(() => {}));
    apiMocks.itemCostHistory.mockReset().mockImplementation(
      (_item: string, _branch: string, _page: number, _per: number, types?: string[]) =>
        Promise.resolve(
          history(
            types?.length
              ? [row('adj-1', 'quantity_adjustment', '-12000')]
              : [row('po-1', 'purchasing', '12000'), row('adj-1', 'quantity_adjustment', '-12000')],
          ),
        ),
    );
  });

  it('offers only the types this item has, with their counts', async () => {
    await openHistory();

    expect(screen.getByRole('button', { name: /^All$/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /Purchase\s*3/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Adjustment\s*2/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Used in production\s*7/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sold/ })).not.toBeInTheDocument();
  });

  it('asks the API for the picked type and shows only those rows', async () => {
    await openHistory();

    fireEvent.click(screen.getByRole('button', { name: /Adjustment\s*2/ }));

    await waitFor(() =>
      expect(apiMocks.itemCostHistory).toHaveBeenLastCalledWith('item-1', 'b1', 1, 50, [
        'quantity_adjustment',
      ]),
    );
    await waitFor(() => expect(screen.queryByText('PO-1')).not.toBeInTheDocument());
    expect(screen.getByText('ADJ-1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Adjustment\s*2/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('All clears the filter', async () => {
    await openHistory();
    fireEvent.click(screen.getByRole('button', { name: /Adjustment\s*2/ }));
    await waitFor(() => expect(screen.queryByText('PO-1')).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^All$/ }));

    await screen.findByText('PO-1');
    expect(apiMocks.itemCostHistory).toHaveBeenLastCalledWith('item-1', 'b1', 1, 50, []);
  });
});
