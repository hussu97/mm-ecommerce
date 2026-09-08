/**
 * Which corner badge a product flies, resolved from its `labels`.
 *
 * A product can carry more than one label — the launch item is both
 * `website_exclusive` and `bestseller` — but the tile has room for one chip. The
 * priority order below is the single place that decides which wins, so the
 * listing, the homepage rail, the cart carousels and the PDP can never disagree.
 * `website_exclusive` leads: a launch item should read as exclusive rather than
 * merely popular.
 *
 * The order mirrors `PRODUCT_LABELS` on the API model; the DB stores labels in
 * this same order, but this resolver does not rely on that — it scans by
 * priority — so a hand-written or migration-written array is judged the same way.
 */
export const BADGE_PRIORITY = [
  'website_exclusive',
  'bestseller',
  'new',
  'limited',
] as const;

export type BadgeVariant = (typeof BADGE_PRIORITY)[number];

/** The winning badge for a product, or null when it flies none. */
export function resolveProductBadge(product: {
  labels?: string[] | null;
}): BadgeVariant | null {
  const labels = product.labels ?? [];
  for (const variant of BADGE_PRIORITY) {
    if (labels.includes(variant)) return variant;
  }
  return null;
}

/** The i18n key for a badge's label text, e.g. `plp.website_exclusive`. */
export function badgeI18nKey(variant: BadgeVariant): string {
  return `plp.${variant}`;
}

/** English fallbacks, for `withFallback` when a translation has not loaded. */
export const BADGE_FALLBACK: Record<BadgeVariant, string> = {
  website_exclusive: 'Website Exclusive',
  bestseller: 'Bestseller',
  new: 'New',
  limited: 'Limited',
};
