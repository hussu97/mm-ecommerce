'use client';

import { usePathname } from 'next/navigation';

import { isPurchaseOrdersTabRoute, PurchaseOrdersTabs } from './PurchaseOrdersTabs';

/**
 * One frame for the purchasing screens. The section title and the tab bar are
 * rendered here, once, above the order list or the supplier list — the same job
 * `InventoryLayout` does for the inventory screens. A PO detail page renders
 * bare: it has its own header (reference, status, back link), and a tab bar
 * above it would light nothing.
 */
export default function PurchaseOrdersLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (!isPurchaseOrdersTabRoute(pathname)) return <>{children}</>;
  return (
    <div>
      <h1 className="font-display text-xl text-primary tracking-wide mb-3">Purchase Orders</h1>
      <PurchaseOrdersTabs />
      {children}
    </div>
  );
}
