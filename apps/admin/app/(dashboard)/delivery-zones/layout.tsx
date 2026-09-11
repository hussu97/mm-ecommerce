import { DeliveryZonesTabs } from './DeliveryZonesTabs';

/**
 * One frame for the three delivery-zone screens. The heading and the tab bar
 * are rendered here, once, above whichever screen is showing.
 */
export default function DeliveryZonesLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <h1 className="font-display text-xl text-gray-800">Delivery Zones</h1>
        <p className="text-xs text-gray-400 font-body mt-1">
          What each area costs to deliver to and who carries it. One map is live at
          a time; to change a price, copy it to a draft and publish the draft.
        </p>
      </div>

      <DeliveryZonesTabs />
      {children}
    </div>
  );
}
