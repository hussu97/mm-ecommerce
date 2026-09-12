import { Suspense } from 'react';

import { RecipeOwnersPage } from '../RecipeOwnersPage';

export default function ProductRecipesPage() {
  return (
    <Suspense>
      <RecipeOwnersPage
        ownerKind="product"
        noun="product item"
        searchPlaceholder="Search products by name or slug…"
      />
    </Suspense>
  );
}
