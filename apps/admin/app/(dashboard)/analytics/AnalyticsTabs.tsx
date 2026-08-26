'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

/**
 * The sub-navigation shared by the two Analytics screens.
 *
 * Analytics answers questions about the past (a date range over `orders`); Live
 * Baskets answers the opposite one — what people are holding right now. They
 * used to be two sidebar items; they are two tabs of one section now. Links, not
 * buttons, because each is its own route, and the active one is decided by the
 * URL. Styling mirrors `AggregatorTabs`/`TabBar` so it reads as one section.
 *
 * `/analytics` is a prefix of `/analytics/carts`, so the active tab is the
 * *most specific* href that covers the path — otherwise standing on Live Baskets
 * would light both.
 */
const TABS = [
  { href: '/analytics', label: 'Analytics' },
  { href: '/analytics/carts', label: 'Live Baskets' },
];

export function AnalyticsTabs() {
  const pathname = usePathname();
  const activeHref = [...TABS]
    .sort((a, b) => b.href.length - a.href.length)
    .find(t => pathname === t.href || pathname.startsWith(`${t.href}/`))?.href;
  return (
    <div
      // Marks this scroller as deliberate, the way `TabBar` does — the mobile
      // audit treats every other horizontal scroller on a phone as a defect.
      data-scroll-intent="tabs"
      className="flex border-b border-gray-200 overflow-x-auto snap-x scrollbar-none"
      role="tablist"
    >
      {TABS.map(tab => {
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
