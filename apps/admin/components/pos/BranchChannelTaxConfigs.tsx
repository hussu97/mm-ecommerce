'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { branchesApi, legalEntitiesApi, taxGroupsApi } from '@/lib/pos-api';
import { ApiError } from '@/lib/api';
import type {
  Branch,
  BranchChannelTaxConfig,
  ChannelClass,
  LegalEntity,
  TaxGroup,
} from '@/lib/pos-types';
import { Button, Select, Spinner } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';

/**
 * Which legal entity each of a branch's sales channels trades under.
 *
 * A branch can bill under more than one licence: Barsha's counter is Najm
 * AlShamal Coffee Shop (not VAT-registered), while its website and aggregator
 * sales are Fatema Cake Sweets (registered). Pick the entity per channel; a
 * channel with no explicit config falls back to the registered default. The
 * entity decides whether VAT is charged and what brand/TRN/logo the receipt
 * shows. Applies to orders created from the next request.
 */

const CHANNELS: { key: ChannelClass; label: string; hint: string }[] = [
  { key: 'counter', label: 'Counter', hint: 'POS / cashier sales at this branch' },
  { key: 'website', label: 'Website', hint: 'Melting Moments storefront orders' },
  {
    key: 'aggregator',
    label: 'Aggregator',
    hint: 'Talabat / Noon / Careem / Deliveroo / Keeta',
  },
];

interface Row {
  legal_entity_id: string;
  tax_group_id: string;
}

function emptyRow(entities: LegalEntity[]): Row {
  // Default to the registered entity where one exists, so a fresh row is never
  // saved pointing at nothing.
  const registered = entities.find((e) => e.vat_registered) ?? entities[0];
  return { legal_entity_id: registered?.id ?? '', tax_group_id: '' };
}

function toRow(c: BranchChannelTaxConfig): Row {
  return { legal_entity_id: c.legal_entity_id, tax_group_id: c.tax_group_id ?? '' };
}

export function BranchChannelTaxConfigs() {
  const toast = useToast();
  // Fetched here, not passed down: the branch list owns its own reloads, so a
  // prop copy would be stale the moment a branch is renamed.
  const [branches, setBranches] = useState<Branch[]>([]);
  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [taxGroups, setTaxGroups] = useState<TaxGroup[]>([]);
  const servable = useMemo(
    () => branches.filter((b) => !b.deleted_at && b.is_active),
    [branches],
  );
  const [branchId, setBranchId] = useState('');
  const [rows, setRows] = useState<Record<ChannelClass, Row> | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    branchesApi
      .list()
      .then(setBranches)
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
    legalEntitiesApi
      .list()
      .then((es) => setEntities(es.filter((e) => e.is_active)))
      .catch(() => setEntities([]));
    taxGroupsApi
      .list()
      .then(setTaxGroups)
      .catch(() => setTaxGroups([]));
  }, []);

  useEffect(() => {
    if (!branchId && servable.length) setBranchId(servable[0].id);
  }, [servable, branchId]);

  const rowsFrom = useCallback(
    (configs: BranchChannelTaxConfig[]): Record<ChannelClass, Row> => {
      const byChannel = new Map(configs.map((c) => [c.channel_class, c]));
      const pick = (ch: ChannelClass) =>
        byChannel.has(ch) ? toRow(byChannel.get(ch)!) : emptyRow(entities);
      return { counter: pick('counter'), website: pick('website'), aggregator: pick('aggregator') };
    },
    [entities],
  );

  const load = useCallback(async () => {
    if (!branchId) return;
    setRows(null);
    try {
      const configs = await branchesApi.channelTaxConfigs(branchId);
      setRows(rowsFrom(configs));
      setError('');
    } catch (err) {
      setRows(rowsFrom([]));
      setError(err instanceof ApiError ? err.message : 'Could not load the configs.');
    }
  }, [branchId, rowsFrom]);

  useEffect(() => {
    load();
  }, [load]);

  const setRow = (channel: ChannelClass, patch: Partial<Row>) =>
    setRows((r) => (r ? { ...r, [channel]: { ...r[channel], ...patch } } : r));

  const save = async () => {
    if (!rows) return;
    if (CHANNELS.some(({ key }) => !rows[key].legal_entity_id)) {
      setError('Pick a legal entity for every channel.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const res = await branchesApi.setChannelTaxConfigs(branchId, {
        configs: CHANNELS.map(({ key }) => ({
          channel_class: key,
          legal_entity_id: rows[key].legal_entity_id,
          tax_group_id: rows[key].tax_group_id || null,
          is_active: true,
        })),
      });
      setRows(rowsFrom(res));
      toast.success('Channel legal entities saved');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed');
      toast.error(err instanceof ApiError ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const entityLabel = (e: LegalEntity) =>
    `${e.brand_name} — ${e.legal_name}${e.vat_registered ? '' : ' (no VAT)'}`;

  if (!servable.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        {error || 'No active branches to configure.'}
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200">
      <header className="px-4 py-3 border-b border-gray-100 flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-body text-gray-800">Legal entity by channel</h2>
          <p className="text-[11px] font-body text-gray-400 mt-1 max-w-2xl">
            Which trade licence each channel trades under. The entity decides whether
            VAT is charged and what brand / TRN / logo the receipt shows — e.g. Barsha&apos;s
            counter is Najm AlShamal (no VAT) while its website and aggregator sales are
            Fatema (registered). Manage the entities on the Legal entities page. Applies
            from the next order.
          </p>
        </div>
        <Button size="sm" onClick={save} loading={saving} disabled={!rows}>
          Save
        </Button>
      </header>

      <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center gap-3">
        <Select
          value={branchId}
          onChange={(e) => setBranchId(e.target.value)}
          options={servable.map((b) => ({ value: b.id, label: b.name }))}
        />
      </div>

      {error && (
        <div className="px-4 py-2 text-xs font-body text-red-600 border-b border-gray-100">
          {error}
        </div>
      )}

      {!rows ? (
        <div className="p-6 flex justify-center">
          <Spinner />
        </div>
      ) : (
        <div className="divide-y divide-gray-100">
          {CHANNELS.map(({ key, label, hint }) => {
            const row = rows[key];
            return (
              <div key={key} className="px-4 py-3">
                <div className="mb-2">
                  <span className="text-sm font-body text-gray-800">{label}</span>
                  <span className="text-[11px] font-body text-gray-400 ml-2">{hint}</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Select
                    label="Legal entity"
                    value={row.legal_entity_id}
                    onChange={(e) => setRow(key, { legal_entity_id: e.target.value })}
                    options={entities.map((e) => ({ value: e.id, label: entityLabel(e) }))}
                  />
                  <Select
                    label="VAT group override (optional)"
                    value={row.tax_group_id}
                    onChange={(e) => setRow(key, { tax_group_id: e.target.value })}
                    options={[
                      { value: '', label: 'Inherit from products' },
                      ...taxGroups.map((g) => ({ value: g.id, label: g.name })),
                    ]}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
