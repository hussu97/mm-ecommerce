import { redirect } from 'next/navigation';

// Delivery Zones opens on the live Map; there is no combined view.
export default function DeliveryZonesIndexPage() {
  redirect('/delivery-zones/map');
}
