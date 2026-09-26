'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { useAuth } from '@/lib/auth-context';
import { cn } from '@/lib/utils';

/**
 * The sub-navigation shared by the purchasing screens.
 *
 * Suppliers used to be a tab of Inventory, but it is the other half of buying:
 * who a purchase order goes to. It sits beside the order list now (the old
 * `/inventory/suppliers` URL redirects here). Links, not buttons, because each
 * is its own route, and the active one is decided by the URL. Styling mirrors
 * `LogsTabs`/`InventoryTabs` so it reads as one section.
 *
 * `/purchase-orders` is a prefix of every route in the section, including the PO
 * detail page, so the list tab matches EXACTLY — otherwise `/purchase-orders/…`
 * would light it. The detail page has no tab at all (it keeps its own back-link
 * header), which is what `isPurchaseOrdersTabRoute` tells the layout.
 */
// `requires` gates a tab on the slug its screen's API needs, the way
// `PosReportsTabs` does: the supplier list is served by `inventory.read`, the
// order list by `inventory.purchase_orders.manage`. A tab a user cannot use is
// not offered, matching how the sidebar hides screens the API would 403.
const TABS: { href: string; label: string; exact?: boolean; requires: string }[] = [
  { href: '/purchase-orders', label: 'Purchase Orders', exact: true, requires: 'inventory.purchase_orders.manage' },
  { href: '/purchase-orders/suppliers', label: 'Suppliers', requires: 'inventory.read' },
  { href: '/purchase-orders/misc-categories', label: 'Misc categories', requires: 'inventory.purchase_orders.manage' },
  { href: '/purchase-orders/misc-periods', label: 'Misc periods', requires: 'inventory.purchase_orders.manage' },
];

function tabFor(pathname: string) {
  return TABS.find((t) =>
    t.exact ? pathname === t.href : pathname === t.href || pathname.startsWith(`${t.href}/`),
  );
}

/** Whether *pathname* is one of the tabbed screens (not a PO detail page). */
export function isPurchaseOrdersTabRoute(pathname: string): boolean {
  return tabFor(pathname) !== undefined;
}

export function PurchaseOrdersTabs() {
  const pathname = usePathname();
  const { user } = useAuth();
  const tabs = TABS.filter(
    (t) => !!user?.is_superadmin || (user?.permissions ?? []).includes(t.requires),
  );
  const activeHref = tabFor(pathname)?.href;
  return (
    <div
      // Marks this scroller as deliberate, the way `TabBar` does — the mobile
      // audit treats every other horizontal scroller on a phone as a defect.
      data-scroll-intent="tabs"
      className="flex border-b border-gray-200 overflow-x-auto snap-x scrollbar-none mb-6"
      role="tablist"
    >
      {tabs.map((tab) => {
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
