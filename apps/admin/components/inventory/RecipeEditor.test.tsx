import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  items: vi.fn(),
  versionedRecipe: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {
    status: number;

    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock('@/lib/pos-api', () => ({
  inventoryApi: {
    items: apiMocks.items,
    versionedRecipe: apiMocks.versionedRecipe,
    expandRecipe: vi.fn(),
    saveRecipeDraft: vi.fn(),
    previewRecipeVersion: vi.fn(),
    activateRecipe: vi.fn(),
  },
}));

import { ApiError } from '@/lib/api';
import { RecipeEditor } from './RecipeEditor';

describe('RecipeEditor', () => {
  beforeEach(() => {
    apiMocks.items.mockReset();
    apiMocks.versionedRecipe.mockReset();
  });

  it('keeps the ingredient catalogue when a new owner has no recipe yet', async () => {
    apiMocks.items.mockResolvedValue([
      {
        id: 'butter-id',
        name: 'Butter',
        sku: 'RM001',
        ingredient_unit: 'g',
        is_active: true,
        deleted_at: null,
      },
    ]);
    apiMocks.versionedRecipe.mockRejectedValue(new ApiError(404, 'Recipe not found'));

    render(<RecipeEditor ownerKind="product" ownerId="product-id" ownerLabel="Fudge Brownie" />);

    expect(await screen.findByText(/No recipe yet/)).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: 'Butter · RM001 · g' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create draft' })).toBeDisabled();
  });
});
