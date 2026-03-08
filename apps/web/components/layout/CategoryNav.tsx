'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import type { Category } from '@/lib/types';
import { localizedField } from '@/lib/i18n/entity';

// ─── Client wrapper — knows which category is active ──────────────────────────

export function CategoryNavLinks({
  categories,
  locale = 'en',
  allLabel = 'All',
  allHref,
}: {
  categories: Category[];
  locale?: string;
  allLabel?: string;
  allHref?: string;
}) {
  const pathname = usePathname();
  const [showFade, setShowFade] = useState(true);

  const handleScroll = (e: React.UIEvent<HTMLUListElement>) => {
    const el = e.currentTarget;
    setShowFade(el.scrollLeft < el.scrollWidth - el.clientWidth - 1);
  };

  return (
    <nav
      aria-label="Category navigation"
      className="hidden sm:block border-b border-gray-100 bg-white relative"
    >
      <ul
        className="max-w-7xl mx-auto px-4 flex items-center gap-6 h-9 overflow-x-auto scrollbar-none"
        onScroll={handleScroll}
      >
        {allHref && (
          <li className="shrink-0">
            <Link
              href={allHref}
              prefetch={true}
              className={[
                'font-body text-[11px] uppercase tracking-widest transition-colors whitespace-nowrap',
                pathname.startsWith(`/${locale}/all-products`)
                  ? 'text-primary font-semibold'
                  : 'text-gray-500 hover:text-primary',
              ].join(' ')}
            >
              {allLabel}
            </Link>
          </li>
        )}
        {categories.map((cat) => {
          const { slug } = cat;
          const name = localizedField(cat, 'name', cat.name, locale);
          const active = pathname === `/${locale}/${slug}` || pathname.startsWith(`/${locale}/${slug}/`);
          return (
            <li key={slug} className="shrink-0">
              <Link
                href={`/${locale}/${slug}`}
                prefetch={true}
                className={[
                  'font-body text-[11px] uppercase tracking-widest transition-colors whitespace-nowrap',
                  active ? 'text-primary font-semibold' : 'text-gray-500 hover:text-primary',
                ].join(' ')}
              >
                {name}
              </Link>
            </li>
          );
        })}
      </ul>
      {/* Right-side fade overlay — shown when more content is scrollable */}
      {showFade && (
        <div
          className="absolute right-0 top-0 bottom-0 w-12 bg-gradient-to-l from-white to-transparent pointer-events-none"
          aria-hidden="true"
        />
      )}
    </nav>
  );
}
