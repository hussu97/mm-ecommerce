import type { Schemas } from '@mm/types';

/**
 * The number to show for an order: the ticket a local-first register printed
 * (`T1-0042`) when there is one, otherwise the server's order number. The
 * customer holds the printed one, so it is what staff search and read out.
 */
export function shownOrderNumber(
  order: { order_number: string } & Partial<Pick<Schemas['OrderListResponse'], 'display_number'>>,
): string {
  return order.display_number || order.order_number;
}
