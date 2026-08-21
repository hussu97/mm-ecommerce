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
 * path looks like a category page is only kept while that category is one the
 * storefront is actually serving; everything else — `/about`, a product page,
 * an external campaign URL — is left alone, because we have no opinion about
 * those and should not invent one.
 */

/**
 * The single-segment paths that are pages in their own right.
 *
 * This is the list migration 120 made necessary. Category slugs used to be
 * `cat-`-prefixed, so "does this link point at a category" was a regex and
 * `/about` could never be mistaken for one. Readable slugs give that up:
 * `/en/brownies` and `/en/about` are the same shape, and only the route table
 * knows which is which.
 *
 * Every directory under `app/[locale]/` except `[category]` itself, and it has
 * to stay that way — a new top-level page added without a line here reads as a
 * category, fails the liveness check, and quietly vanishes from the home page.
 * Its opposite number is `RESERVED_SLUGS` in `category_service.py`, which stops
 * a category from claiming one of these in the first place.
 */
const RESERVED_PATHS: ReadonlySet<string> = new Set([
  'about',
  'account',
  'all-products',
  'blog',
  'cart',
  'checkout',
  'contact',
  'faq',
  'forgot-password',
  'login',
  'privacy',
  'reset-password',
  'search',
  'signup',
  'track',
]);

const LOCALES: ReadonlySet<string> = new Set(
  (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? 'en,ar').split(','),
);

/**
 * The category a link points at, or null when it points at something else.
 *
 * "Something else" is everything that is not a bare category page: an external
 * URL, one of the reserved pages above, or a deeper path like
 * `/en/brownies/classic-brownie`. That last one matters — a product link is not
 * a category link, and treating its final segment as a slug would judge every
 * hand-linked product against the category list and drop it.
 *
 * Query strings, fragments and trailing slashes are stripped first: a
 * hand-typed `/desserts/` or `/desserts?utm=x` is the same dead link as the
 * clean one, and matching only the tidy form would leave the exact cases a
 * human typed unguarded.
 */
export function categorySlugOf(href: string | undefined | null): string | null {
  if (!href) return null;

  // Internal paths only. An absolute URL is somebody's campaign landing page or
  // an Instagram link, and the catalogue has nothing to say about it.
  if (!href.startsWith('/')) return null;

  const path = href.split('?')[0].split('#')[0].replace(/\/+$/, '');
  const segments = path.split('/').filter(Boolean);
  if (segments.length && LOCALES.has(segments[0].toLowerCase())) segments.shift();

  if (segments.length !== 1) return null;

  const slug = segments[0].toLowerCase();
  return RESERVED_PATHS.has(slug) ? null : slug;
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
