import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/pos-api', () => ({ inventoryApi: { checkPurchaseLineCosts: vi.fn() } }));

import { LineCostComparison, type LineCostCheck } from './LineCostComparison';

const CHECK: LineCostCheck = {
  item_id: 'item-1',
  unit_cost: '12.5000000000',
  last_purchase: {
    purchase_order_id: 'po-9',
    reference: 'PO-000123',
    supplier_name: 'Dairy Co',
    branch_name: 'Sharjah',
    purchased_on: '2026-10-01',
    quantity: '10',
    unit_cost: '12.0000000000',
  },
  vs_last_purchase: { reference_cost: '12', difference: '0.5', change: '0.0417', verdict: 'worse' },
  average_cost: '13.0000000000',
  vs_average_cost: { reference_cost: '13', difference: '-0.5', change: '-0.0385', verdict: 'better' },
};

describe('LineCostComparison', () => {
  it('links the last purchase in a new tab and shows both verdicts', () => {
    render(<LineCostComparison check={CHECK} unit="kg" loading={false} failed={false} />);
    const link = screen.getByRole('link', { name: /PO-000123/ });
    expect(link).toHaveAttribute('href', '/purchase-orders/po-9');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(screen.getByText('4.2% worse')).toBeInTheDocument();
    expect(screen.getByText('3.9% better')).toBeInTheDocument();
    expect(screen.getByText(/Dairy Co · Sharjah/)).toBeInTheDocument();
  });

  it('says what is unknown instead of showing a zero', () => {
    render(
      <LineCostComparison
        check={{ item_id: 'item-1', unit_cost: null, last_purchase: null, average_cost: null }}
        unit="kg"
        loading={false}
        failed={false}
      />,
    );
    expect(screen.getByText('No earlier purchase of this item')).toBeInTheDocument();
    expect(screen.getByText('No stock on hand to average')).toBeInTheDocument();
    expect(screen.getByText('Enter a quantity and total to compare.')).toBeInTheDocument();
  });

  it('reports a failed lookup rather than rendering nothing', () => {
    render(<LineCostComparison check={undefined} unit="kg" loading={false} failed />);
    expect(screen.getByText(/Couldn’t load past prices/)).toBeInTheDocument();
  });
});
