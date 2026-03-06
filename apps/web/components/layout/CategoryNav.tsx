'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { Category } from '@/lib/types';

// ─── Client wrapper — knows which category is active ──────────────────────────

export function CategoryNavLinks({ categories }: { categories: Category[] }) {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Category navigation"
      className="hidden sm:block border-b border-gray-100 bg-white"
    >
      <ul className="max-w-7xl mx-auto px-4 flex items-center gap-6 h-9 overflow-x-auto scrollbar-none">
        {categories.map(({ slug, name }) => {
          const active = pathname === `/${slug}` || pathname.startsWith(`/${slug}/`);
          return (
            <li key={slug} className="shrink-0">
              <Link
                href={`/${slug}`}
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
    </nav>
  );
}
