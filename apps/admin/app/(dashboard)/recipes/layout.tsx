import { RecipesTabs } from './RecipesTabs';

/**
 * One frame for the Recipes screens: the section title and tab bar, rendered
 * once above whichever owner-kind list is showing — mirrors `InventoryLayout`.
 */
export default function RecipesLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <h1 className="font-display text-xl text-primary tracking-wide mb-3">Recipes</h1>
      <RecipesTabs />
      {children}
    </div>
  );
}
