'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { branchesApi, taxGroupsApi } from '@/lib/pos-api';
import { ApiError } from '@/lib/api';
import type {
  Branch,
  BranchChannelTaxConfig,
  ChannelClass,
  TaxGroup,
} from '@/lib/pos-types';
import { Button, Input, Select, Spinner } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';

/**
 * A branch's VAT + trade-license identity, per sales channel.
 *
 * A branch can trade under more than one license: Barsha's counter is not
 * VAT-registered (under the threshold), while its website and aggregator sales
 * are, under the Melting Moments license. Each row here is one channel; leaving
 * a channel VAT-registered with blank identity is the default (it inherits the
 * branch's own tax number / name), so this only needs editing where a channel
 * genuinely differs.
 *
 * Changes apply to orders created from the next request — an order already
 * placed keeps the identity frozen onto it.
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
  vat_registered: boolean;
  tax_group_id: string;
  tax_number: string;
  tax_registration_name: string;
  invoice_title: string;
}

function emptyRow(): Row {
  return {
    vat_registered: true,
    tax_group_id: '',
    tax_number: '',
    tax_registration_name: '',
    invoice_title: '',
  };
}

function toRow(c: BranchChannelTaxConfig): Row {
  return {
    vat_registered: c.vat_registered,
    tax_group_id: c.tax_group_id ?? '',
    tax_number: c.tax_number ?? '',
    tax_registration_name: c.tax_registration_name ?? '',
    invoice_title: c.invoice_title ?? '',
  };
}

export function BranchChannelTaxConfigs() {
  const toast = useToast();
  // Fetched here, not passed down: the branch list owns its own reloads, so a
  // prop copy would be stale the moment a branch is renamed.
  const [branches, setBranches] = useState<Branch[]>([]);
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
    taxGroupsApi
      .list()
      .then(setTaxGroups)
      .catch(() => setTaxGroups([]));
  }, []);

  useEffect(() => {
    if (!branchId && servable.length) setBranchId(servable[0].id);
  }, [servable, branchId]);

  const load = useCallback(async () => {
    if (!branchId) return;
    setRows(null);
    try {
      const configs = await branchesApi.channelTaxConfigs(branchId);
      const byChannel = new Map(configs.map((c) => [c.channel_class, c]));
      setRows({
        counter: byChannel.has('counter') ? toRow(byChannel.get('counter')!) : emptyRow(),
        website: byChannel.has('website') ? toRow(byChannel.get('website')!) : emptyRow(),
        aggregator: byChannel.has('aggregator')
          ? toRow(byChannel.get('aggregator')!)
          : emptyRow(),
      });
      setError('');
    } catch (err) {
      setRows({ counter: emptyRow(), website: emptyRow(), aggregator: emptyRow() });
      setError(err instanceof ApiError ? err.message : 'Could not load the tax configs.');
    }
  }, [branchId]);

  useEffect(() => {
    load();
  }, [load]);

  const setRow = (channel: ChannelClass, patch: Partial<Row>) =>
    setRows((r) => (r ? { ...r, [channel]: { ...r[channel], ...patch } } : r));

  const save = async () => {
    if (!rows) return;
    setSaving(true);
    setError('');
    try {
      const res = await branchesApi.setChannelTaxConfigs(branchId, {
        configs: CHANNELS.map(({ key }) => {
          const row = rows[key];
          const trimmed = (s: string) => (s.trim() ? s.trim() : null);
          return {
            channel_class: key,
            vat_registered: row.vat_registered,
            tax_group_id: row.tax_group_id || null,
            tax_number: trimmed(row.tax_number),
            tax_registration_name: trimmed(row.tax_registration_name),
            invoice_title: trimmed(row.invoice_title),
            is_active: true,
          };
        }),
      });
      const byChannel = new Map(res.map((c) => [c.channel_class, c]));
      setRows({
        counter: byChannel.has('counter') ? toRow(byChannel.get('counter')!) : emptyRow(),
        website: byChannel.has('website') ? toRow(byChannel.get('website')!) : emptyRow(),
        aggregator: byChannel.has('aggregator')
          ? toRow(byChannel.get('aggregator')!)
          : emptyRow(),
      });
      toast.success('Channel tax configs saved');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed');
      toast.error(err instanceof ApiError ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

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
          <h2 className="text-sm font-body text-gray-800">VAT & trade license by channel</h2>
          <p className="text-[11px] font-body text-gray-400 mt-1 max-w-2xl">
            The VAT treatment and the legal identity printed on each channel&apos;s
            invoices. Leave a channel VAT-registered with blank identity to inherit
            this branch&apos;s own tax number and name — only fill a row where a channel
            trades under a different license. A channel switched off VAT charges none and
            must not be titled &quot;Tax Invoice&quot;. Applies from the next order.
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
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div>
                    <span className="text-sm font-body text-gray-800">{label}</span>
                    <span className="text-[11px] font-body text-gray-400 ml-2">{hint}</span>
                  </div>
                  <label className="flex items-center gap-2 text-xs font-body text-gray-700">
                    <input
                      type="checkbox"
                      checked={row.vat_registered}
                      onChange={(e) =>
                        setRow(key, { vat_registered: e.target.checked })
                      }
                    />
                    VAT-registered
                  </label>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
                  <Input
                    placeholder="Tax registration name (legal entity)"
                    value={row.tax_registration_name}
                    onChange={(e) =>
                      setRow(key, { tax_registration_name: e.target.value })
                    }
                  />
                  <Input
                    placeholder={
                      row.vat_registered ? 'TRN (tax number)' : 'No TRN (not registered)'
                    }
                    value={row.tax_number}
                    disabled={!row.vat_registered}
                    onChange={(e) => setRow(key, { tax_number: e.target.value })}
                  />
                  <Input
                    placeholder={
                      row.vat_registered ? 'Invoice title (e.g. Tax Invoice)' : 'Invoice'
                    }
                    value={row.invoice_title}
                    onChange={(e) => setRow(key, { invoice_title: e.target.value })}
                  />
                  <Select
                    value={row.tax_group_id}
                    disabled={!row.vat_registered}
                    onChange={(e) => setRow(key, { tax_group_id: e.target.value })}
                    options={[
                      { value: '', label: 'VAT group: inherit from products' },
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
