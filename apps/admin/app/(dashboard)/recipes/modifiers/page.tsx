import { Suspense } from 'react';

import { RecipeOwnersPage } from '../RecipeOwnersPage';

export default function ModifierRecipesPage() {
  return (
    <Suspense>
      <RecipeOwnersPage
        ownerKind="modifier_option"
        noun="modifier item"
        searchPlaceholder="Search modifier options by name or SKU…"
      />
    </Suspense>
  );
}
