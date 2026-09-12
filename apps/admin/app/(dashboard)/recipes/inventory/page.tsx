import { Suspense } from 'react';

import { RecipeOwnersPage } from '../RecipeOwnersPage';

export default function InventoryRecipesPage() {
  return (
    <Suspense>
      <RecipeOwnersPage
        ownerKind="inventory_item"
        noun="inventory item"
        showKind
        searchPlaceholder="Search made items by name or SKU…"
      />
    </Suspense>
  );
}
