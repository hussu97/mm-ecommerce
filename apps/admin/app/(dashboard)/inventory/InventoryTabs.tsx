'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

/**
 * The sub-navigation shared by the inventory screens.
 *
 * Items, Ledger, Counts and the rest used to be `useState` tabs within one
 * ~1500-line page, so a refresh always dropped you back on Items. They are
 * separate routes now, so these are links rather than buttons — the active one
 * is decided by the URL, and a refresh keeps you where you were. Styling mirrors
 * `LogsTabs`/`AggregatorTabs`/`TabBar` so the screens read as one section.
 *
 * Several hrefs share the `/inventory` prefix (and `/inventory/transfers` is a
 * prefix of the transfer detail route), so the active tab is the *most specific*
 * href that covers the path — otherwise a detail page would light no tab and a
 * shorter href could win over a longer one.
 */
const TABS = [
  { href: '/inventory/items', label: 'Items' },
  { href: '/inventory/ledger', label: 'Ledger' },
  { href: '/inventory/counts', label: 'Counts' },
  { href: '/inventory/templates', label: 'Report templates' },
  { href: '/inventory/submissions', label: 'Report submissions' },
  { href: '/inventory/transfers', label: 'Transfers' },
  { href: '/inventory/suppliers', label: 'Suppliers' },
  { href: '/inventory/categories', label: 'Categories' },
  { href: '/inventory/integrity', label: 'Integrity' },
];

export function InventoryTabs() {
  const pathname = usePathname();
  const activeHref = [...TABS]
    .sort((a, b) => b.href.length - a.href.length)
    .find((t) => pathname === t.href || pathname.startsWith(`${t.href}/`))?.href;
  return (
    <div
      // Marks this scroller as deliberate, the way `TabBar` does — the mobile
      // audit treats every other horizontal scroller on a phone as a defect.
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
