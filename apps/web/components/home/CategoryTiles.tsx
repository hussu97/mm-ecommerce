import Image from 'next/image';
import { PromotionLink } from '@/components/analytics/PromotionLink';
import { localizedField } from '@/lib/i18n/entity';
import { Reveal } from './Reveal';
import type { Category } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

export interface CategoryTile {
  /** Category slug, e.g. `brownies`. Also drives the link target. */
  slug?: string;
  label?: string;
  image?: string;
  /** Overrides the `/{locale}/{slug}` destination. */
  href?: string;
  /** Line under the label. Defaults to the live product count. */
  meta?: string;
}

export interface CategoriesContent {
  title?: string;
  subtitle?: string;
  tiles?: CategoryTile[];
  /** How many categories to show when no tiles are configured. */
  limit?: number;
  /** Noun after the auto product count, e.g. "treats". Blank hides the count. */
  count_suffix?: string;
}

/** Repo-shipped tile art, keyed by the tail of the category slug. */
function fallbackImage(slug: string): string {
  return `/images/tiles/tile-${slug.replace(/^cat-/, '')}.jpg`;
}

interface ResolvedTile {
  key: string;
  href: string;
  label: string;
  image: string;
  meta: string;
}

export function resolveCategoryTiles(
  c: CategoriesContent,
  categories: Category[],
  locale: string,
): ResolvedTile[] {
  const bySlug = new Map(categories.map(cat => [cat.slug, cat]));

  const source: CategoryTile[] =
    c.tiles?.length
      ? c.tiles
      : categories.slice(0, c.limit ?? 8).map(cat => ({ slug: cat.slug }));

  return source
    .map(tile => {
      const cat = tile.slug ? bySlug.get(tile.slug) : undefined;
      // A tile tied to a category is storefront content for that category.
      // Never use a CMS fallback link to revive it after the category has
      // been hidden; only truly standalone tiles may rely on an explicit URL.
      if (tile.slug && !cat) return null;
      // A configured standalone tile without an explicit link has nowhere to
      // go — drop it rather than render a dead card.
      if (!cat && !tile.href) return null;

      const label =
        tile.label ||
        (cat ? localizedField(cat, 'name', cat.name, locale) : '');
      if (!label) return null;

      const count = cat?.product_count ?? 0;
      const autoMeta =
        c.count_suffix && count > 0 ? `${count} ${c.count_suffix}` : '';

      return {
        key: tile.slug ?? tile.href!,
        href: `/${locale}${tile.href ?? `/${cat!.slug}`}`,
        label,
        image: tile.image || cat?.image_url || fallbackImage(tile.slug ?? ''),
        meta: tile.meta || autoMeta,
      };
    })
    .filter((t): t is ResolvedTile => t !== null);
}

/**
 * "Shop by craving" — the catalogue as pictures instead of a nav list. Every
 * tile is a real product photo, so the grid does the selling that a paragraph
 * of category description used to do badly.
 */
export function CategoryTiles({
  c,
  categories,
  locale,
}: {
  c: CategoriesContent;
  categories: Category[];
  locale: string;
}) {
  const tiles = resolveCategoryTiles(c, categories, locale);
  if (tiles.length === 0) return null;

  return (
    <section aria-label="Shop by category" className="py-14 sm:py-20 bg-white">
      <div className="max-w-7xl mx-auto px-4 sm:px-6">
        {(c.title || c.subtitle) && (
          <Reveal className="mb-8 sm:mb-10 text-center">
            {c.title && (
              <h2 className="font-display text-2xl sm:text-3xl text-gray-800 uppercase tracking-[0.18em]">
                {c.title}
              </h2>
            )}
            {c.subtitle && (
              <p className="font-body text-sm text-gray-500 mt-2.5">{c.subtitle}</p>
            )}
          </Reveal>
        )}

        {/* The lead tile takes a 2×2 block on large screens so the grid reads
            as an editorial spread rather than a sheet of equal thumbnails. */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 sm:gap-4">
          {tiles.map((tile, i) => (
            <Reveal
              key={tile.key}
              delay={Math.min(i, 6) * 60}
              className={i === 0 ? 'lg:col-span-2 lg:row-span-2' : ''}
            >
              <PromotionLink
                href={tile.href}
                creative="category_tile"
                slot={i}
                title={tile.label}
                className="group relative block h-full overflow-hidden bg-[#f4ece4]"
              >
                <div className="relative aspect-square">
                  <Image
                    src={tile.image}
                    alt={tile.label}
                    fill
                    sizes={i === 0 ? '(max-width: 1024px) 50vw, 50vw' : '(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 25vw'}
                    className="object-cover transition-transform duration-[900ms] ease-out group-hover:scale-[1.07]"
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-black/55 via-black/5 to-transparent" />
                  <div className="absolute inset-x-0 bottom-0 p-3.5 sm:p-5 flex items-end justify-between gap-2">
                    <div>
                      <h3 className="font-display text-white text-sm sm:text-lg uppercase tracking-[0.14em] leading-tight">
                        {tile.label}
                      </h3>
                      {tile.meta && (
                        <p className="font-body text-[10px] sm:text-xs text-white/75 tracking-widest uppercase mt-1">
                          {tile.meta}
                        </p>
                      )}
                    </div>
                    <Icon name="arrow_forward" className="text-white/85 text-lg sm:text-xl rtl:rotate-180 translate-x-0 group-hover:translate-x-1 transition-transform duration-300" />
                  </div>
                </div>
              </PromotionLink>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
