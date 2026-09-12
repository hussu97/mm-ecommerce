'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

/**
 * Sub-navigation for the Recipes section — one tab per recipe-owner kind.
 *
 * Recipes used to be edited inline on three unrelated screens (a product's edit
 * page, the modifiers page, the inventory items page). They are one section now:
 * separate routes so a refresh keeps you where you were, styled to mirror
 * `InventoryTabs`/`LogsTabs` so the console reads as one system.
 */
const TABS = [
  { href: '/recipes/products', label: 'Product Items' },
  { href: '/recipes/modifiers', label: 'Modifier Items' },
  { href: '/recipes/inventory', label: 'Inventory Items' },
];

export function RecipesTabs() {
  const pathname = usePathname();
  const activeHref = [...TABS]
    .sort((a, b) => b.href.length - a.href.length)
    .find((t) => pathname === t.href || pathname.startsWith(`${t.href}/`))?.href;
  return (
    <div
      data-scroll-intent="tabs"
      className="flex border-b border-gray-200 overflow-x-auto snap-x scrollbar-none mb-6"
      role="tablist"
    >
      {TABS.map((tab) => {
        const active = tab.href === activeHref;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            role="tab"
            aria-selected={active}
            className={cn(
              'shrink-0 snap-start whitespace-nowrap px-4 min-h-11 md:min-h-0 md:py-2 flex items-center text-xs font-body font-medium uppercase tracking-wider transition-colors border-b-2 -mb-px',
              active
                ? 'text-primary border-primary'
                : 'text-gray-500 border-transparent hover:text-gray-700',
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </div>
  );
}
