'use client';

import { useEffect, useState } from 'react';

import { deliveryZonesApi, ApiError } from '@/lib/api';
import type { DeliveryZoneMap } from '@/lib/types';
import { Spinner } from '@/components/ui';
import { ZoneMap } from '@/components/delivery/ZoneMap';

/**
 * The live delivery map. Fetches only the published map — the fees table and
 * the estimates each load what they need on their own route, which is cheaper
 * than the old page's all-in-one load that every tab paid for.
 */
export default function DeliveryMapPage() {
  const [zoneMap, setZoneMap] = useState<DeliveryZoneMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      setZoneMap(await deliveryZonesApi.map());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load delivery maps.');
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Spinner />
      </div>
    );
  }

  if (error) {
    return <div className="text-sm text-red-500 font-body">{error}</div>;
  }

  if (!zoneMap) return null;

  return (
    <div className="bg-white border border-gray-200 p-4 mb-4">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-3">
        {zoneMap.version?.name ?? 'No map published'}
      </p>
      <ZoneMap data={zoneMap} />
    </div>
  );
}
