/**
 * Whether a piece of CMS-authored home-page content still leads anywhere.
 *
 * The home page sells categories through three different blocks, and only one
 * of them knows it. Category tiles carry a `slug` and were taught to disappear
 * with their category. Hero slides and promo bands carry a bare `cta_href`
 * typed in by hand — structurally they are "a photo and a link", so nothing
 * connected them to the catalogue, and hiding every dessert left a full-width
 * band on the home page inviting customers to shop desserts. It linked to a
 * category page that no longer lists anything.
 *
 * The connection is in the URL, so that is where it gets made. A link whose
 * last path segment looks like a category is only kept while that category is
 * one the storefront is actually serving; everything else — `/all-products`,
 * `/about`, an external campaign URL — is left alone, because we have no
 * opinion about those and should not invent one.
 */

/** Category slugs are `cat-`-prefixed by convention, and the route is `/{locale}/{slug}`. */
const CATEGORY_SLUG = /^cat-[a-z0-9-]+$/i;

/**
 * The category a link points at, or null when it points at something else.
 *
 * Query strings and trailing slashes are stripped first: a hand-typed
 * `/cat-desserts/` or `/cat-desserts?utm=x` is the same dead link as the clean
 * one, and matching only the tidy form would leave the exact cases a human
 * typed unguarded.
 */
export function categorySlugOf(href: string | undefined | null): string | null {
  if (!href) return null;
  const path = href.split('?')[0].split('#')[0].replace(/\/+$/, '');
  const last = path.split('/').pop() ?? '';
  return CATEGORY_SLUG.test(last) ? last.toLowerCase() : null;
}

/**
 * True when this link is safe to render.
 *
 * Non-category links always pass. A category link passes only while its
 * category is live — which, since the API stopped returning categories with
 * nothing shoppable in them, means "there is something behind this button".
 */
export function isLiveLink(
  href: string | undefined | null,
  liveCategorySlugs: ReadonlySet<string>,
): boolean {
  const slug = categorySlugOf(href);
  return slug === null || liveCategorySlugs.has(slug);
}

/** The live slugs, in the shape the checks above want. */
export function liveSlugSet(categories: ReadonlyArray<{ slug: string }>): Set<string> {
  return new Set(categories.map(c => c.slug.toLowerCase()));
}
