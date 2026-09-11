'use client';

import { DeliveryEstimates } from '@/components/delivery/DeliveryEstimates';

/**
 * Courier estimates. Not gated on a published map: what a courier promises is
 * true whether or not a map has been published, and an estate with no live map
 * is exactly when somebody is setting these up. `DeliveryEstimates` fetches its
 * own data.
 */
export default function DeliveryEstimatesPage() {
  return <DeliveryEstimates />;
}
