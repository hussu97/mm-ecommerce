import { redirect } from 'next/navigation';

// Moved into the Purchase Orders section (Suppliers tab). Kept as a redirect so
// old bookmarks and muscle-memory URLs still land.
export default function InventorySuppliersRedirect() {
  redirect('/purchase-orders/suppliers');
}
