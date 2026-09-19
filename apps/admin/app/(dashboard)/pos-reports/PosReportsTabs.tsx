'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';

import { useAuth } from '@/lib/auth-context';
import { cn } from '@/lib/utils';

/**
 * The sub-navigation shared by the POS report screens.
 *
 * These used to be `TabBar` buttons over one page's `useState`; they are tabs of
 * one section now, each its own route, and the active one is decided by the URL —
 * links, not buttons. Styling mirrors `LogsTabs` so it reads as one section.
 *
 * The Payments, Taxes, Inventory and Suppliers tabs were retired (the first two
 * unused, the latter two showed duplicated/incorrect figures — the live
 * Inventory section is the source of truth); Sales and the Email report remain.
 *
 * The reporting window rides in the query string (`?from=&to=&branch=`), so the
 * tab links carry it along — switching tabs keeps the same window.
 *
 * `/pos-reports` is a prefix of every tab, so the active tab is the *most
 * specific* href that covers the path. (`/pos-reports` itself only ever
 * redirects to `/pos-reports/sales`.)
 */
// `requires` gates a tab on a permission slug of its own (the VAT report needs
// `reports.vat`, a different slug from the `reports.sales` that opens the section)
// — a `null`/absent requirement rides the section's own gate. A tab a user cannot
// use is not offered, matching how the sidebar hides screens the API would 403.
const TABS: { href: string; label: string; requires?: string }[] = [
  { href: '/pos-reports/sales', label: 'Sales' },
  { href: '/pos-reports/email', label: 'Email Report' },
  { href: '/pos-reports/vat', label: 'VAT', requires: 'reports.vat' },
];

export function PosReportsTabs() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { user } = useAuth();
  const query = searchParams.toString();
  const tabs = TABS.filter(
    (t) => !t.requires || !!user?.is_superadmin || (user?.permissions ?? []).includes(t.requires),
  );
  const activeHref = [...tabs]
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
      {tabs.map(tab => {
        const active = tab.href === activeHref;
        return (
          <Link
            key={tab.href}
            href={query ? `${tab.href}?${query}` : tab.href}
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
