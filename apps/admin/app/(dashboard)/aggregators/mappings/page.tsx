'use client';

import { useCallback, useEffect, useState } from 'react';

import { branchMapApi, ApiError } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Schemas } from '@mm/types';
import type { BranchMapChannel } from '@/lib/types';
import type { Branch } from '@/lib/pos-types';

// Row/input shapes are the generated contract (rule 8); `BranchMapChannel` stays
// local because the contract types `channel` as a bare `string`.
type BranchMapRow = Schemas['AggregatorBranchMapOut'];
type BranchMapInput = Schemas['AggregatorBranchMapIn'];
import { Badge, Button, Input, LoadError, Select, Spinner } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { Modal } from '@/components/pos/ResourcePage';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { AggregatorTabs } from '../AggregatorTabs';

/**
 * The outlet↔branch map an operator maintains by hand.
 *
 * Every `aggregator_branch_map` row: the ids a marketplace uses for one of our
 * branches (its outlet, brand and company), so an inbound order or a
 * reconciliation feed can be tied back to the branch it belongs to. A branch a
 * channel has no row for is a branch that channel's orders arrive against
 * blank — which is what this screen exists to fix.
 *
 * The write is an upsert on the pair (channel, branch): editing keeps the pair
 * fixed and changes the ids; adding picks a new pair. Toggling Active is the
 * same upsert with the flag flipped, so the row never has to be deleted to be
 * paused.
 */

// The five marketplaces, plus an "all" sentinel for the filter — the empty
// string `buildQs` drops rather than sends, same as the reconciliation screen.
const CHANNEL_FILTER_OPTIONS = [
  { value: '', label: 'All channels' },
  { value: 'careem', label: 'Careem' },
  { value: 'deliveroo', label: 'Deliveroo' },
  { value: 'talabat', label: 'Talabat' },
  { value: 'noon', label: 'noon' },
  { value: 'keeta', label: 'Keeta' },
];

// The form has no "all" — a mapping belongs to exactly one channel.
const CHANNEL_OPTIONS = CHANNEL_FILTER_OPTIONS.slice(1);

/** Prettify a channel code for a badge without a lookup table. */
function channelName(code: string): string {
  return code === 'noon' ? 'noon' : code.charAt(0).toUpperCase() + code.slice(1);
}

/** What the form holds while it is open — strings throughout, blanks for absent ids. */
interface FormState {
  channel: BranchMapChannel;
  branch_id: string;
  external_outlet_id: string;
  external_brand_id: string;
  external_company_id: string;
  channel_ref: string;
  is_active: boolean;
}

const EMPTY_FORM: FormState = {
  channel: 'careem',
  branch_id: '',
  external_outlet_id: '',
  external_brand_id: '',
  external_company_id: '',
  channel_ref: '',
  is_active: true,
};

/** A blank string is "no id", which the API stores as null rather than "". */
function blankToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

export default function MappingsPage() {
  const toast = useToast();
  const confirm = useConfirm();

  const [branches, setBranches] = useState<Branch[]>([]);
  const [channel, setChannel] = useState('');

  const [rows, setRows] = useState<BranchMapRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  // The open form: `null` closed, a row when editing, `EMPTY_FORM` when adding.
  const [editing, setEditing] = useState<BranchMapRow | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);
  // Which row's Active toggle is mid-flight, so only that switch is disabled.
  const [togglingId, setTogglingId] = useState<string | null>(null);

  useEffect(() => {
    void branchesApi
      .list()
      .then(list => setBranches(list.filter(b => !b.deleted_at)))
      .catch(() => setBranches([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await branchMapApi.list({ channel: channel || undefined });
      setRows(data);
      setLoadError('');
    } catch (e) {
      setRows([]);
      setLoadError(e instanceof Error ? e.message : 'Could not load the mappings');
    } finally {
      setLoading(false);
    }
  }, [channel]);

  useEffect(() => {
    void load();
  }, [load]);

  const branchLabel = (id: string) => {
    const b = branches.find(x => x.id === id);
    return b ? `${b.reference} · ${b.name}` : id;
  };

  function openAdd() {
    setEditing(null);
    setForm({ ...EMPTY_FORM });
  }

  function openEdit(row: BranchMapRow) {
    setEditing(row);
    setForm({
      // The contract types `channel` as a bare string; the form's select is
      // driven by the `BranchMapChannel` union, and a stored row only ever holds
      // one of those values.
      channel: row.channel as BranchMapChannel,
      branch_id: row.branch_id,
      external_outlet_id: row.external_outlet_id ?? '',
      external_brand_id: row.external_brand_id ?? '',
      external_company_id: row.external_company_id ?? '',
      channel_ref: row.channel_ref ?? '',
      is_active: row.is_active,
    });
  }

  function closeForm() {
    setForm(null);
    setEditing(null);
  }

  async function save() {
    if (!form) return;
    if (!form.branch_id) {
      toast.error('Choose a branch for this mapping.');
      return;
    }
    setSaving(true);
    try {
      const body: BranchMapInput = {
        channel: form.channel,
        branch_id: form.branch_id,
        external_outlet_id: blankToNull(form.external_outlet_id),
        external_brand_id: blankToNull(form.external_brand_id),
        external_company_id: blankToNull(form.external_company_id),
        channel_ref: blankToNull(form.channel_ref),
        is_active: form.is_active,
      };
      await branchMapApi.upsert(body);
      toast.success(editing ? 'Mapping updated.' : 'Mapping saved.');
      closeForm();
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Could not save the mapping.');
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(row: BranchMapRow) {
    setTogglingId(row.id);
    try {
      await branchMapApi.upsert({
        channel: row.channel,
        branch_id: row.branch_id,
        external_outlet_id: row.external_outlet_id,
        external_brand_id: row.external_brand_id,
        external_company_id: row.external_company_id,
        channel_ref: row.channel_ref,
        is_active: !row.is_active,
      });
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Could not change the status.');
    } finally {
      setTogglingId(null);
    }
  }

  async function remove(row: BranchMapRow) {
    const ok = await confirm({
      title: 'Delete mapping',
      message: `Remove the ${channelName(row.channel)} mapping for ${
        row.branch_name ?? branchLabel(row.branch_id)
      }? That channel's orders for this branch will arrive unmapped until it is added again.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await branchMapApi.remove(row.id);
      toast.success('Mapping deleted.');
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Could not delete the mapping.');
    }
  }

  const columns: DataColumn<BranchMapRow>[] = [
    {
      header: 'Branch',
      priority: 'primary',
      render: r => (
        <span className="font-medium text-gray-800">
          {r.branch_name ?? branchLabel(r.branch_id)}
        </span>
      ),
    },
    {
      header: 'Channel',
      render: r => <Badge variant="neutral">{channelName(r.channel)}</Badge>,
    },
    {
      header: 'Outlet id',
      render: r =>
        r.external_outlet_id ? (
          <code className="text-xs text-gray-600">{r.external_outlet_id}</code>
        ) : (
          <span className="text-gray-300">—</span>
        ),
    },
    {
      header: 'Brand id',
      render: r =>
        r.external_brand_id ? (
          <code className="text-xs text-gray-600">{r.external_brand_id}</code>
        ) : (
          <span className="text-gray-300">—</span>
        ),
    },
    {
      header: 'Company id',
      render: r =>
        r.external_company_id ? (
          <code className="text-xs text-gray-600">{r.external_company_id}</code>
        ) : (
          <span className="text-gray-300">—</span>
        ),
    },
    {
      header: 'Active',
      render: r => (
        <label className="inline-flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={r.is_active}
            disabled={togglingId === r.id}
            onChange={() => void toggleActive(r)}
            className="accent-primary"
          />
          <span className="text-xs font-body text-gray-500">
            {r.is_active ? 'Active' : 'Inactive'}
          </span>
        </label>
      ),
    },
    {
      header: 'Actions',
      className: 'text-right whitespace-nowrap',
      render: r => (
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => openEdit(r)}>
            Edit
          </Button>
          <Button variant="danger" size="sm" onClick={() => void remove(r)}>
            Delete
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <AggregatorTabs />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Mappings</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            {rows.length} mapping{rows.length === 1 ? '' : 's'} · which branch each
            marketplace&rsquo;s outlet points at
          </p>
        </div>
        <Button onClick={openAdd}>
          <span className="material-icons text-[16px]">add</span>
          Add mapping
        </Button>
      </div>

      {/* Filter — the channel, same shape as the reconciliation screen. */}
      <div className="flex flex-wrap gap-3">
        <div className="w-44">
          <Select
            value={channel}
            onChange={e => setChannel(e.target.value)}
            options={CHANNEL_FILTER_OPTIONS}
          />
        </div>
      </div>

      {loadError && <LoadError message={loadError} onRetry={load} />}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <DataTable<BranchMapRow>
          columns={columns}
          rows={rows}
          rowKey={r => r.id}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">
              No mappings for these filters. Add one to tie a branch to a marketplace outlet.
            </p>
          }
        />
      )}

      {form && (
        <Modal title={editing ? 'Edit mapping' : 'Add mapping'} onClose={closeForm}>
          <div className="space-y-4">
            <Select
              label="Channel"
              value={form.channel}
              onChange={e => setForm({ ...form, channel: e.target.value as BranchMapChannel })}
              options={CHANNEL_OPTIONS}
              // The pair (channel, branch) is the key the upsert writes on;
              // changing it on an existing row would create a second mapping
              // rather than edit this one, so it is fixed once saved.
              disabled={editing !== null}
            />
            <Select
              label="Branch"
              value={form.branch_id}
              onChange={e => setForm({ ...form, branch_id: e.target.value })}
              options={branches.map(b => ({ value: b.id, label: `${b.reference} · ${b.name}` }))}
              placeholder="Choose a branch…"
              disabled={editing !== null}
            />
            <Input
              label="Outlet id"
              value={form.external_outlet_id}
              onChange={e => setForm({ ...form, external_outlet_id: e.target.value })}
              placeholder="The marketplace's id for this outlet"
            />
            <Input
              label="Brand id"
              value={form.external_brand_id}
              onChange={e => setForm({ ...form, external_brand_id: e.target.value })}
              placeholder="Optional"
            />
            <Input
              label="Company id"
              value={form.external_company_id}
              onChange={e => setForm({ ...form, external_company_id: e.target.value })}
              placeholder="Optional"
            />
            <Input
              label="Channel reference"
              value={form.channel_ref}
              onChange={e => setForm({ ...form, channel_ref: e.target.value })}
              placeholder="Optional free-form reference"
            />
            <label className="flex items-center gap-2 text-sm font-body text-gray-700">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={e => setForm({ ...form, is_active: e.target.checked })}
                className="h-4 w-4 accent-primary"
              />
              <span>Active</span>
            </label>

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={closeForm} disabled={saving}>
                Cancel
              </Button>
              <Button onClick={() => void save()} loading={saving}>
                {editing ? 'Save changes' : 'Add mapping'}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
