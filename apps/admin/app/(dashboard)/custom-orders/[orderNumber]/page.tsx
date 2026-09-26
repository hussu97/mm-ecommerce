import { redirect } from 'next/navigation';

/**
 * A custom order has no page of its own any more: it is an order, and the one
 * order page (`/orders/[orderNumber]`) carries its pack, courier, recipe,
 * invoice and edit controls alongside everything every other order shows. This
 * route stays so links already sent (emails, bookmarks, the registers) land.
 */
export default async function CustomOrderRedirect({
  params,
}: {
  params: Promise<{ orderNumber: string }>;
}) {
  const { orderNumber } = await params;
  let decoded = orderNumber;
  try {
    decoded = decodeURIComponent(orderNumber);
  } catch {
    // A malformed escape: pass the segment on as it came.
  }
  redirect(`/orders/${encodeURIComponent(decoded)}`);
}
