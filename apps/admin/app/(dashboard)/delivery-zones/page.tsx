'use client';

import { useEffect, useState } from 'react';
import { deliveryZonesApi, ApiError } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import type {
  DeliverySettings,
  DeliveryMapVersion,
  DeliveryZoneMap,
  } from '@/lib/types';
import { Spinner, TabBar } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { DeliveryEstimates } from '@/components/delivery/DeliveryEstimates';
import { ZoneMap } from '@/components/delivery/ZoneMap';

import { PolygonTable } from './components/PolygonTable';
import { SettingsCard } from './components/SettingsCard';

export default function DeliveryZonesPage() {
  const toast = useToast();
  const confirm = useConfirm();
  const [versions, setVersions] = useState<DeliveryMapVersion[]>([]);
  const [settings, setSettings] = useState<DeliverySettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [tab, setTab] = useState('map');
  const [zoneMap, setZoneMap] = useState<DeliveryZoneMap | null>(null);
  // Which kitchen bakes a zone's orders. Needed here rather than on the branch
  // page because the choice belongs to the shape, not to the branch.
  const [branches, setBranches] = useState<Branch[]>([]);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [v, s, m, br] = await Promise.all([
        deliveryZonesApi.listVersions(),
        deliveryZonesApi.getSettings(),
        deliveryZonesApi.map(),
        branchesApi.list(),
      ]);
      setVersions(v);
      setSettings(s);
      setZoneMap(m);
      setBranches(br.filter(x => x.is_active && !x.deleted_at));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load delivery maps.');
    } finally {
      setLoading(false);
    }
  }

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function createDraft(sourceId: string) {
    const name = draftName.trim();
    if (!name) {
      toast.error('Give the draft a name so it can be told apart from the live map.');
      return;
    }
    await run(async () => {
      await deliveryZonesApi.createVersion({
        name,
        source_version_id: sourceId,
      });
      setDraftName('');
    });
  }

  async function publishVersion(version: DeliveryMapVersion) {
    if (await confirm({
      title: 'Publish map',
      message: `Publish "${version.name}"? Every new order is priced from it immediately.`,
      confirmLabel: 'Publish',
    })) void run(() => deliveryZonesApi.publish(version.id));
  }

  async function deleteVersion(version: DeliveryMapVersion) {
    if (await confirm({
      title: 'Delete draft',
      message: `Delete the draft "${version.name}"?`,
      confirmLabel: 'Delete',
      danger: true,
    })) void run(() => deliveryZonesApi.deleteVersion(version.id));
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

  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <h1 className="font-display text-xl text-gray-800">Delivery Zones</h1>
        <p className="text-xs text-gray-400 font-body mt-1">
          What each area costs to deliver to and who carries it. One map is live at
          a time; to change a price, copy it to a draft and publish the draft.
        </p>
      </div>

      <TabBar
        tabs={[
          { key: 'map', label: 'Map' },
          { key: 'maps', label: 'Fees & couriers', count: versions.length },
          { key: 'estimates', label: 'Estimates' },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === 'map' && zoneMap && (
        <div className="bg-white border border-gray-200 p-4 mb-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-3">
            {zoneMap.version?.name ?? 'No map published'}
          </p>
          <ZoneMap data={zoneMap} />
        </div>
      )}

      {/* Not gated on `zoneMap`: what a courier promises is true whether or not
          a map has been published, and an estate with no live map is exactly
          when somebody is setting these up. */}
      {tab === 'estimates' && <DeliveryEstimates />}

      {tab === 'maps' && settings && (
        <div className="space-y-3">
          <SettingsCard
            settings={settings}
            busy={busy}
            onSave={data => run(() => deliveryZonesApi.updateSettings(data))}
          />
          <PolygonTable
            versions={versions}
            branches={branches}
            busy={busy}
            draftName={draftName}
            onDraftNameChange={setDraftName}
            onCopy={createDraft}
            onPublish={publishVersion}
            onDelete={deleteVersion}
          />
        </div>
      )}
    </div>
  );
}
