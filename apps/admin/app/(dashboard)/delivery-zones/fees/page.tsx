'use client';

import { useEffect, useState } from 'react';

import { deliveryZonesApi, ApiError } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import type { DeliverySettings, DeliveryMapVersion } from '@/lib/types';
import { Spinner } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';

import { PolygonTable } from '../components/PolygonTable';
import { SettingsCard } from '../components/SettingsCard';

/**
 * Fees & couriers: the three delivery numbers that belong to no zone, plus the
 * per-zone fees-and-couriers table and the draft/publish lifecycle. Loads the
 * settings, the version list and the (kitchen) branches — the map screen and
 * the estimates screen fetch their own data, so nothing is loaded that this tab
 * does not use.
 */
export default function DeliveryFeesPage() {
  const toast = useToast();
  const confirm = useConfirm();
  const [versions, setVersions] = useState<DeliveryMapVersion[]>([]);
  const [settings, setSettings] = useState<DeliverySettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [draftName, setDraftName] = useState('');
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
      const [v, s, br] = await Promise.all([
        deliveryZonesApi.listVersions(),
        deliveryZonesApi.getSettings(),
        branchesApi.list(),
      ]);
      setVersions(v);
      setSettings(s);
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

  if (!settings) return null;

  return (
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
  );
}
