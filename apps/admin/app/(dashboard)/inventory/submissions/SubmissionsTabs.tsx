'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

/**
 * The sub-navigation inside Report submissions.
 *
 * The page used to be one screen with a shared branch filter and three stacked
 * tables (shift reports, transfer/return documents, manual counts). They are
 * three sub-routes now, each with its own filter bar, so a refresh keeps you on
 * the sub-tab you were reading. Links, not buttons, because each is its own
 * route; styling mirrors `InventoryTabs`/`LogsTabs` so it reads as one section.
 *
 * `/inventory/submissions` is a prefix of the other two, so the active tab is
 * the *most specific* href covering the path — otherwise the default (Shift
 * reports) would light on every sub-tab. There is no redirect: the index route
 * IS the Shift reports tab.
 */
const TABS = [
  { href: '/inventory/submissions', label: 'Shift reports' },
  { href: '/inventory/submissions/transfers', label: 'Transfers & returns' },
  { href: '/inventory/submissions/counts', label: 'Manual stock counts' },
];

export function SubmissionsTabs() {
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
