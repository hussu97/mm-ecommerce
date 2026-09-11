'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

/**
 * The sub-navigation shared by the three delivery-zone screens.
 *
 * The live map, the fees-and-couriers table and the courier estimates used to
 * be three `TabBar` buttons over one page's `useState`. They are three tabs of
 * one section now, each its own route, and the active one is decided by the
 * URL — links, not buttons. Styling mirrors `LogsTabs` so it reads as one
 * section.
 *
 * `/delivery-zones` is a prefix of every tab, so the active tab is the *most
 * specific* href that covers the path. (`/delivery-zones` itself only ever
 * redirects to `/delivery-zones/map`.)
 */
const TABS = [
  { href: '/delivery-zones/map', label: 'Map' },
  { href: '/delivery-zones/fees', label: 'Fees & couriers' },
  { href: '/delivery-zones/estimates', label: 'Estimates' },
];

export function DeliveryZonesTabs() {
  const pathname = usePathname();
  const activeHref = [...TABS]
    .sort((a, b) => b.href.length - a.href.length)
    .find(t => pathname === t.href || pathname.startsWith(`${t.href}/`))?.href;
  return (
    <div
      // Marks this scroller as deliberate, the way `TabBar` does — the mobile
      // audit treats every other horizontal scroller on a phone as a defect.
      data-scroll-intent="tabs"
      className="flex border-b border-gray-200 overflow-x-auto snap-x scrollbar-none mb-6"
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
