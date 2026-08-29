'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

/**
 * The sub-navigation shared by the aggregator screens.
 *
 * The console's `TabBar` switches a piece of state within one page; these tabs
 * are separate routes, so they are links rather than buttons — the active one
 * is decided by the URL, not by a `useState`. The styling deliberately
 * mirrors `TabBar` (the same underline, the same uppercase label) so the
 * screens read as one section with tabs rather than unrelated pages.
 */
const TABS = [
  { href: '/aggregators/runs', label: 'Runs' },
  { href: '/aggregators/reconciliation', label: 'Reconciliation' },
  { href: '/aggregators/mappings', label: 'Branch Map' },
  { href: '/aggregators/item-mappings', label: 'Item Mappings' },
  { href: '/aggregators/logins', label: 'Logins' },
];

export function AggregatorTabs() {
  const pathname = usePathname();
  return (
    <div
      // Marks this scroller as deliberate, the same way `TabBar` does — the
      // mobile audit treats every other horizontal scroller on a phone as a defect.
      data-scroll-intent="tabs"
      className="flex border-b border-gray-200 overflow-x-auto snap-x scrollbar-none"
      role="tablist"
    >
      {TABS.map(tab => {
        const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
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
