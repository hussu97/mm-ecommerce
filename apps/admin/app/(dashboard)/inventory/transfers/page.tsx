'use client';

// The transfer-TEMPLATE configurator: the reusable per-source-branch pick lists
// a register draws on to raise an inter-branch transfer. The transfer & return
// LOG that used to sit beneath it has moved to the Report submissions tab, next
// to the shift reports, so this route is now just the configurator.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  branchesApi,
  inventoryApi,
  type TransferTemplate,
  type TransferTemplateWrite,
} from '@/lib/pos-api';
import type { Branch, InventoryItem } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, LoadError, Select } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';
import { BranchFilter } from '../_shared';

export default function TransfersPage() {
  const [branchId, setBranchId] = useState('');
  const [branches, setBranches] = useState<Branch[]>([]);
  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);
  const branchName = useCallback(
    (id: string) => branches.find((b) => b.id === id)?.name ?? id,
    [branches],
  );

  return (
    <div className="max-w-[1500px] space-y-8">
      <BranchFilter value={branchId} onChange={setBranchId} />
      <p className="text-sm text-gray-500">
        Choose a branch first. Its transfer templates are the reusable pick lists a register draws on to raise an inter-branch transfer. The transfer &amp; return log lives under <strong>Report submissions</strong>.
      </p>
      <TransferTemplatesSection branchId={branchId} branches={branches} branchName={branchName} />
    </div>
  );
}

function TransferTemplatesSection({ branchId, branches, branchName }: {
  branchId: string;
  branches: Branch[];
  branchName: (id: string) => string;
}) {
  const [templates, setTemplates] = useState<TransferTemplate[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [destinationBranchId, setDestinationBranchId] = useState('');
  const [displayOrder, setDisplayOrder] = useState(0);
  const [isActive, setIsActive] = useState(true);
  const [selectedItems, setSelectedItems] = useState<string[]>([]);
  const [itemSearch, setItemSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  const selectableItems = useMemo(() => {
    const search = itemSearch.trim().toLocaleLowerCase();
    return items
      .filter((item) => item.is_active && !item.deleted_at)
      .filter((item) => !search || `${item.name} ${item.sku}`.toLocaleLowerCase().includes(search))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [items, itemSearch]);

  const reload = useCallback(async () => {
    if (!branchId) { setTemplates([]); setLoadError(false); return; }
    setLoadError(false);
    try {
      setTemplates(await inventoryApi.transferTemplates(branchId));
    } catch {
      setTemplates([]);
      setLoadError(true);
    }
  }, [branchId]);

  useEffect(() => {
    void inventoryApi.items().then(setItems).catch(() => setItems([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!branchId) { setTemplates([]); setLoadError(false); return () => { cancelled = true; }; }
    setLoadError(false);
    inventoryApi.transferTemplates(branchId)
      .then((rows) => { if (!cancelled) setTemplates(rows); })
      .catch(() => { if (!cancelled) { setTemplates([]); setLoadError(true); } });
    return () => { cancelled = true; };
  }, [branchId]);

  const resetForm = useCallback(() => {
    setEditingId(null);
    setName('');
    setDestinationBranchId('');
    setDisplayOrder(0);
    setIsActive(true);
    setSelectedItems([]);
  }, []);

  // Switching branch abandons any in-progress edit — a template belongs to its
  // source branch, so its item list and destination make no sense under another.
  useEffect(() => { resetForm(); setMessage(null); }, [branchId, resetForm]);

  const startEdit = (template: TransferTemplate) => {
    setEditingId(template.id);
    setName(template.name);
    setDestinationBranchId(template.destination_branch_id ?? '');
    setDisplayOrder(template.display_order);
    setIsActive(template.is_active);
    setSelectedItems([...template.items].sort((a, b) => a.display_order - b.display_order).map((item) => item.item_id));
    setMessage(null);
  };

  const save = async () => {
    if (!branchId || !name.trim() || selectedItems.length === 0) return;
    setSaving(true);
    setMessage(null);
    const body: TransferTemplateWrite = {
      source_branch_id: branchId,
      destination_branch_id: destinationBranchId || null,
      name: name.trim(),
      is_active: isActive,
      display_order: displayOrder,
      items: selectedItems.map((itemId, index) => ({ item_id: itemId, display_order: index })),
    };
    try {
      if (editingId) await inventoryApi.updateTransferTemplate(editingId, body);
      else await inventoryApi.createTransferTemplate(body);
      setMessage({ text: editingId ? 'Template updated.' : 'Template created.', error: false });
      resetForm();
      await reload();
    } catch (error) {
      setMessage({ text: error instanceof ApiError ? error.message : 'Could not save the template. Please try again.', error: true });
    } finally {
      setSaving(false);
    }
  };

  // The latest revision of each lineage — a lineage being (source branch, name).
  // Registers only ever see the latest active revision, so "current" is the
  // highest version_number per lineage (mirrors templates/page.tsx). Templates
  // are append-only: PUT creates a new version rather than editing in place.
  const latestTemplateIds = useMemo(() => {
    const latestByLineage = new Map<string, TransferTemplate>();
    for (const template of templates) {
      const key = `${template.source_branch_id}::${template.name}`;
      const current = latestByLineage.get(key);
      if (!current || template.version_number > current.version_number) {
        latestByLineage.set(key, template);
      }
    }
    return new Set(Array.from(latestByLineage.values(), (template) => template.id));
  }, [templates]);

  const deactivate = async (template: TransferTemplate) => {
    setSaving(true);
    setMessage(null);
    try {
      await inventoryApi.deactivateTransferTemplate(template.id);
      if (editingId === template.id) resetForm();
      setMessage({ text: `${template.name} v${template.version_number} deactivated. Registers will no longer offer this pick list.`, error: false });
      await reload();
    } catch (error) {
      setMessage({ text: error instanceof ApiError ? error.message : 'Could not deactivate the template. Please try again.', error: true });
    } finally {
      setSaving(false);
    }
  };

  const destinationOptions = branches.filter((b) => b.id !== branchId);

  return (
    <div className="border border-gray-200 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-medium text-gray-800">Transfer templates</h3>
        <Badge>{templates.length} template{templates.length === 1 ? '' : 's'}</Badge>
      </div>
      {!branchId ? (
        <p className="border border-dashed border-gray-300 p-3 text-sm text-gray-500">Choose a branch above to configure its transfer templates.</p>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-4">
            <Input label="Template name" value={name} onChange={(event) => setName(event.target.value)} />
            <Select
              label="Destination branch"
              value={destinationBranchId}
              onChange={(event) => setDestinationBranchId(event.target.value)}
              placeholder="Any branch"
              options={destinationOptions.map((b) => ({ value: b.id, label: b.name }))}
            />
            <Input label="Display order (low first)" type="number" value={String(displayOrder)} onChange={(event) => setDisplayOrder(Number(event.target.value) || 0)} />
            <label className="flex items-center gap-2 pt-7 text-sm"><input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} />Active</label>
          </div>
          <div className="flex flex-wrap items-end justify-between gap-2">
            <label className="block flex-1 text-xs uppercase tracking-wider text-gray-500">Items in this template
              <Input aria-label="Search transfer-template items" value={itemSearch} onChange={(event) => setItemSearch(event.target.value)} placeholder="Search name or SKU" className="mt-1" />
            </label>
            <span className="pb-2 text-xs text-gray-500">{selectedItems.length} selected · sorted by name</span>
          </div>
          <select multiple value={selectedItems} onChange={(event) => setSelectedItems(Array.from(event.target.selectedOptions, (option) => option.value))} className="min-h-44 w-full border border-gray-300 bg-white p-2 text-sm">
            {selectableItems.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku}</option>)}
          </select>
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-500">Select multiple items with Shift/Cmd.</span>
            <div className="flex gap-2">
              {editingId && <Button variant="outline" onClick={resetForm} disabled={saving}>Cancel edit</Button>}
              <Button onClick={() => void save()} loading={saving} disabled={!branchId || selectedItems.length === 0 || !name.trim()}>{editingId ? 'Save changes' : 'Create template'}</Button>
            </div>
          </div>
          {message && <p className={`p-2 text-sm ${message.error ? 'bg-red-50 text-red-800' : 'bg-green-50 text-green-800'}`}>{message.text}</p>}
          {loadError && (
            <LoadError
              message="This branch's transfer templates could not be loaded. It may already have some — do not create a new one until this clears."
              onRetry={() => void reload()}
            />
          )}
          {!loadError && templates.length === 0 && <p className="border border-dashed border-gray-300 p-3 text-sm text-gray-500">No transfer templates for this branch yet. Create the first one above.</p>}
          {templates.length > 0 && <DataTable rows={templates} rowKey={(row) => row.id} columns={[
            { header: 'Name', priority: 'primary', render: (row) => row.name },
            { header: 'Destination', render: (row) => row.destination_branch_id ? branchName(row.destination_branch_id) : <span className="text-gray-400">Any</span> },
            { header: 'Items', render: (row) => row.items.length },
            { header: 'Order', render: (row) => row.display_order },
            { header: 'Version', render: (row) => `v${row.version_number}` },
            { header: 'POS status', render: (row) => latestTemplateIds.has(row.id) ? row.is_active ? <Badge variant="success">Current</Badge> : <Badge variant="neutral">Deactivated</Badge> : <Badge variant="neutral">Superseded</Badge> },
          ]} actions={(row) => (
            <>
              <RowAction onClick={() => startEdit(row)}>Edit</RowAction>
              {latestTemplateIds.has(row.id) && row.is_active && <RowAction disabled={saving} onClick={() => void deactivate(row)}>Deactivate</RowAction>}
            </>
          )} />}
        </>
      )}
    </div>
  );
}
