/**
 * The courier vocabularies the three parts of this screen share: how a
 * provider is named, how its own status words read, and which of those words
 * mean the box arrived.
 *
 * `PROVIDER_LABEL` sat in `DeliveryPanel` and was read by the page and the
 * fulfilment dialog too — the kind of shared constant that keeps a 1,583-line
 * file from coming apart.
 */

// Lalamove shouts, noon Send does not, so the two cannot collide.
export const COURIER_STATUS_LABEL: Record<string, string> = {
  ASSIGNING_DRIVER: 'Finding a driver',
  ON_GOING: 'Driver on the way to us',
  PICKED_UP: 'Collected',
  COMPLETED: 'Delivered',
  CANCELED: 'Cancelled',
  REJECTED: 'Rejected by drivers',
  EXPIRED: 'Expired — nobody accepted',
  created: 'Task created',
  pending_assignment: 'Finding a rider',
  assigned: 'Rider on the way to us',
  arrived_at_pickup_location: 'Rider at the kitchen',
  picked_up: 'Collected',
  arrived_at_delivery: 'Rider at the door',
  delivered: 'Delivered',
  undelivered: 'Could not be handed over',
  cancelled: 'Cancelled',
};

export const DELIVERED_STATUSES = new Set(['COMPLETED', 'delivered']);

export const PROVIDER_LABEL: Record<string, string> = {
  lalamove: 'Lalamove',
  noon_send: 'noon Send',
  slider: 'Slider',
  third_party: 'Third party',
};
