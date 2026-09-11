import { InventoryTabs } from './InventoryTabs';

/**
 * One frame for the inventory screens. The section title and the tab bar are
 * rendered here, once, above whichever inventory route is showing — the same
 * job `LogsLayout` does for the log screens.
 */
export default function InventoryLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <h1 className="font-display text-xl text-primary tracking-wide mb-3">Inventory</h1>
      <InventoryTabs />
      {children}
    </div>
  );
}
