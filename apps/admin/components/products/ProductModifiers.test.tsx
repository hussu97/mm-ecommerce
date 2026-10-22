import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  updateOption: vi.fn(),
  updateModifierLink: vi.fn(),
  list: vi.fn(),
  success: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {},
  modifiersApi: { list: apiMocks.list, updateOption: apiMocks.updateOption },
  productsApi: { updateModifierLink: apiMocks.updateModifierLink },
}));
vi.mock('@/components/ui/feedback', () => ({
  useToast: () => ({ success: apiMocks.success, error: vi.fn() }),
  useConfirm: () => vi.fn(),
}));
// The per-branch grid fetches branches on mount; it is not what is under test.
vi.mock('@/components/products/BranchStock', () => ({
  ModifierOptionStockGrid: () => null,
}));

import { ProductModifiers } from './ProductModifiers';
import type { Modifier, Product } from '@/lib/types';

function modifier(price = 5): Modifier {
  return {
    id: 'mod-1',
    reference: 'FLAV',
    name: 'Flavours',
    translations: {},
    is_active: true,
    options: [
      { id: 'opt-1', modifier_id: 'mod-1', name: 'Fudge', translations: {}, sku: 'F-1', price, calories: null, is_active: true, display_order: 0 },
      { id: 'opt-2', modifier_id: 'mod-1', name: 'Lotus', translations: {}, sku: 'L-1', price: 0, calories: null, is_active: false, display_order: 1 },
    ],
  };
}

function product(overrides: Partial<Product['product_modifiers'][number]> = {}): Product {
  return {
    slug: 'brownie-box',
    product_modifiers: [
      {
        id: 'pm-1',
        modifier_id: 'mod-1',
        modifier: modifier(),
        minimum_options: 3,
        maximum_options: 3,
        free_options: 0,
        unique_options: false,
        display_order: 0,
        ...overrides,
      },
    ],
  } as unknown as Product;
}

describe('ProductModifiers', () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((m) => m.mockReset());
    apiMocks.list.mockResolvedValue([]);
  });

  it('lists every option with its price in an editable field, inactive ones too', () => {
    render(<ProductModifiers product={product()} />);
    expect(screen.getByLabelText('Price of Fudge')).toHaveValue(5);
    expect(screen.getByLabelText('Price of Lotus')).toHaveValue(0);
    expect(screen.getByRole('switch', { name: 'Lotus inactive' })).toHaveAttribute('aria-checked', 'false');
  });

  it('saves only the changed price, and Enter saves the group rather than the form', async () => {
    apiMocks.updateOption.mockResolvedValue(modifier(6.5));
    const submitted = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(
      <form onSubmit={submitted}>
        <ProductModifiers product={product()} />
      </form>,
    );
    const price = screen.getByLabelText('Price of Fudge');
    fireEvent.change(price, { target: { value: '6.5' } });
    expect(screen.getByText('was 5.00')).toBeInTheDocument();
    expect(screen.getByText(/1 unsaved change/)).toBeInTheDocument();

    fireEvent.keyDown(price, { key: 'Enter' });
    await waitFor(() => expect(apiMocks.updateOption).toHaveBeenCalledTimes(1));
    expect(apiMocks.updateOption).toHaveBeenCalledWith('mod-1', 'opt-1', { price: 6.5 });
    expect(apiMocks.updateModifierLink).not.toHaveBeenCalled();
    expect(submitted).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument());
    expect(screen.getByLabelText('Price of Fudge')).toHaveValue(6.5);
  });

  it('refuses a malformed price without calling the API', async () => {
    render(<ProductModifiers product={product()} />);
    fireEvent.change(screen.getByLabelText('Price of Fudge'), { target: { value: '1.234' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/at most two decimals/)).toBeInTheDocument();
    expect(apiMocks.updateOption).not.toHaveBeenCalled();
  });

  it('edits the link rules in place and turns an option back on', async () => {
    apiMocks.updateModifierLink.mockResolvedValue(product({ maximum_options: 6 }));
    apiMocks.updateOption.mockResolvedValue({
      ...modifier(),
      options: modifier().options.map((o) => ({ ...o, is_active: true })),
    });
    render(<ProductModifiers product={product()} />);

    fireEvent.change(screen.getAllByRole('spinbutton')[1], { target: { value: '6' } });
    fireEvent.click(screen.getByRole('switch', { name: 'Lotus inactive' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(apiMocks.updateOption).toHaveBeenCalled());
    expect(apiMocks.updateModifierLink).toHaveBeenCalledWith('brownie-box', 'mod-1', {
      minimum_options: 3,
      maximum_options: 6,
      free_options: 0,
      unique_options: false,
    });
    expect(apiMocks.updateOption).toHaveBeenCalledWith('mod-1', 'opt-2', { is_active: true });
    await waitFor(() => expect(apiMocks.success).toHaveBeenCalledWith('Flavours: 2 changes saved.'));
  });

  it('will not save a minimum above the maximum', async () => {
    render(<ProductModifiers product={product()} />);
    fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Min cannot be more than max.')).toBeInTheDocument();
    expect(apiMocks.updateModifierLink).not.toHaveBeenCalled();
  });
});
