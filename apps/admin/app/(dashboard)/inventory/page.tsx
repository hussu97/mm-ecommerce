import { redirect } from 'next/navigation';

// The Inventory section opens on the Items tab; there is no combined view.
export default function InventoryIndexPage() {
  redirect('/inventory/items');
}
