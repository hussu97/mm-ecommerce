import { redirect } from 'next/navigation';

/** The section lands on the first tab. */
export default function RecipesIndexPage() {
  redirect('/recipes/products');
}
