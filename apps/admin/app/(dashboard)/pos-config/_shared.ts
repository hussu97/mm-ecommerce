// Shared constants for the POS Configuration tabs.

// `dine_in`/`drive_thru` were retired when the register dropped its order-type
// choice — scoping a rule to them would match nothing. `pickup` (every counter
// order, plus online store-pickup) and `delivery` (online/aggregator) remain.
export const ORDER_TYPE_OPTIONS = [
  { value: 'pickup', label: 'Pickup' },
  { value: 'delivery', label: 'Delivery' },
];
