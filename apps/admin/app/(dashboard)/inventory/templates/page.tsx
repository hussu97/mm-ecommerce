'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { inventoryApi, type ReportTemplate } from '@/lib/pos-api';
import type { InventoryItem } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, LoadError, Select } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import {
  BranchFilter,
  REPORT_FILL_ORDER,
  REPORT_TEMPLATE_GUIDANCE,
  type ReportTemplateKind,
} from '../_shared';

export default function ReportTemplatesPage() {
  const [branchId, setBranchId] = useState('');
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [name, setName] = useState('Closing stock reconciliation');
  const [reportType, setReportType] = useState<ReportTemplateKind>('finished_goods');
  const [cadence, setCadence] = useState('per_business_day');
  const [displayOrder, setDisplayOrder] = useState<number>(REPORT_FILL_ORDER.finished_goods);
  const [required, setRequired] = useState(true);
  const [selectedItems, setSelectedItems] = useState<string[]>([]);
  const [itemSearch, setItemSearch] = useState('');
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [savingTemplate, setSavingTemplate] = useState(false);
  // Whether the templates fetch failed. Without this an errored load is
  // indistinguishable from a branch with no templates, and the "create the
  // first version" prompt below invited a duplicate on top of a 500 (F-ADM-8).
  const [loadError, setLoadError] = useState(false);

  const guidance = REPORT_TEMPLATE_GUIDANCE[reportType];
  const suggestedItems = useMemo(
    () => items
      .filter((item) => item.is_active && !item.deleted_at && guidance.kinds.includes(item.kind))
      .sort((a, b) => a.count_order - b.count_order || a.name.localeCompare(b.name)),
    [guidance.kinds, items],
  );
  const selectableItems = useMemo(() => {
    const search = itemSearch.trim().toLocaleLowerCase();
    const candidates = guidance.kinds.length > 0 ? suggestedItems : items.filter((item) => item.is_active && !item.deleted_at);
    return candidates.filter((item) => !search || `${item.name} ${item.sku} ${item.storage_zone ?? ''}`.toLocaleLowerCase().includes(search));
  }, [guidance.kinds.length, itemSearch, items, suggestedItems]);

  const reload = useCallback(async () => {
    if (!branchId) {
      setTemplates([]);
      setLoadError(false);
      return;
    }
    setLoadError(false);
    try {
      setTemplates(await inventoryApi.reportTemplates(branchId));
    } catch {
      // Keep the list empty but remember it is empty because the load FAILED,
      // not because the branch has none — the render below leans on that.
      setTemplates([]);
      setLoadError(true);
    }
  }, [branchId]);

  useEffect(() => {
    let cancelled = false;
    if (!branchId) {
      setTemplates([]);
      setLoadError(false);
      return () => { cancelled = true; };
    }
    setLoadError(false);
    inventoryApi.reportTemplates(branchId)
      .then((reportTemplates) => { if (!cancelled) setTemplates(reportTemplates); })
      .catch(() => { if (!cancelled) { setTemplates([]); setLoadError(true); } });
    inventoryApi.items().then(setItems).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [branchId]);

  const createTemplate = async () => {
    if (!branchId || selectedItems.length === 0 || !name.trim()) return;
    setSavingTemplate(true);
    setMessage(null);
    try {
      await inventoryApi.createReportTemplate({
        branch_id: branchId,
        name: name.trim(),
        report_type: reportType,
        cadence: cadence as 'per_till' | 'per_business_day' | 'ad_hoc',
        is_required: required,
        is_active: true,
        display_order: displayOrder,
        configuration: { visible_columns: ['opening', 'movements', 'expected', 'physical', 'variance', 'remark'] },
        approval_cost_threshold: '100',
        approval_variance_percent: '10',
        items: selectedItems.map((itemId, index) => ({
          item_id: itemId,
          display_order: index,
          required_input: guidance.requiredInput,
        })),
      });
      setMessage({ text: 'Template created. It will appear in the next matching POS checklist.', error: false });
      setSelectedItems([]);
      await reload();
    } catch (error) {
      setMessage({ text: error instanceof ApiError ? error.message : 'Could not create the template. Please try again.', error: true });
    } finally {
      setSavingTemplate(false);
    }
  };

  const applySuggestion = () => {
    setName(guidance.defaultName);
    setCadence(guidance.cadence);
    setDisplayOrder(REPORT_FILL_ORDER[reportType]);
    setRequired(guidance.required);
    setSelectedItems(suggestedItems.map((item) => item.id));
    setMessage(null);
  };

  const latestTemplateIds = useMemo(() => {
    const latestByType = new Map<string, ReportTemplate>();
    for (const template of templates) {
      const current = latestByType.get(template.report_type);
      if (!current || template.version_number > current.version_number) {
        latestByType.set(template.report_type, template);
      }
    }
    return new Set(Array.from(latestByType.values(), (template) => template.id));
  }, [templates]);

  const deactivateTemplate = async (template: ReportTemplate) => {
    setSavingTemplate(true);
    setMessage(null);
    try {
      await inventoryApi.deactivateReportTemplate(template.id);
      setMessage({ text: `${template.name} v${template.version_number} is deactivated. POS will no longer create this report.`, error: false });
      await reload();
    } catch (error) {
      setMessage({ text: error instanceof ApiError ? error.message : 'Could not deactivate the template. Please try again.', error: true });
    } finally {
      setSavingTemplate(false);
    }
  };

  return <div className="max-w-[1400px] space-y-5">
    <BranchFilter value={branchId} onChange={setBranchId} />
    <p className="text-sm text-gray-500">Choose a branch first: its templates own their own item list. POS receives only the latest active version of each report type for that branch. Outstanding reports remain visible after the till closes.</p>
    <div className="border border-gray-200 p-4 space-y-3">
      <div className="flex items-center justify-between"><h3 className="font-medium text-gray-800">Branch report templates</h3><Badge>{templates.length} active/versioned</Badge></div>
      <div className="grid gap-3 md:grid-cols-4">
        <Input label="Template name" value={name} onChange={(event) => setName(event.target.value)} />
        <Select label="Type" value={reportType} onChange={(event) => { const next = event.target.value as ReportTemplateKind; setReportType(next); setDisplayOrder(REPORT_FILL_ORDER[next]); setSelectedItems([]); setMessage(null); }} options={[
          { value: 'production', label: 'Production' }, { value: 'finished_goods', label: 'Finished goods' }, { value: 'raw_materials', label: 'Raw materials' }, { value: 'packaging', label: 'Packaging' }, { value: 'spot_check', label: 'Spot check' },
        ]} />
        <Select label="Cadence" value={cadence} onChange={(event) => setCadence(event.target.value)} options={[
          { value: 'per_till', label: 'At till close (per till)' }, { value: 'per_business_day', label: 'At end of day (per business day)' }, { value: 'ad_hoc', label: 'Ad hoc (manual only)' },
        ]} />
        <Input label="Fill order (low first)" type="number" value={String(displayOrder)} onChange={(event) => setDisplayOrder(Number(event.target.value) || 0)} />
        <label className="flex items-center gap-2 pt-7 text-sm"><input type="checkbox" checked={required} onChange={(event) => setRequired(event.target.checked)} />Required (may be deferred/waived)</label>
      </div>
      <div className="border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
        <p className="font-medium">What staff will do</p>
        <p className="mt-1">{guidance.staffInstruction}</p>
        <Button type="button" variant="outline" size="sm" className="mt-3 bg-white" onClick={applySuggestion} disabled={guidance.kinds.length === 0}>
          Use suggested {guidance.kinds.length > 0 ? `${suggestedItems.length}-item set` : 'item set'}
        </Button>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <label className="block flex-1 text-xs uppercase tracking-wider text-gray-500">Items in physical count order
          <Input aria-label="Search report-template items" value={itemSearch} onChange={(event) => setItemSearch(event.target.value)} placeholder="Search name, SKU or storage zone" className="mt-1" />
        </label>
        <span className="pb-2 text-xs text-gray-500">{selectedItems.length} selected · sorted by count order</span>
      </div>
      <select multiple value={selectedItems} onChange={(event) => setSelectedItems(Array.from(event.target.selectedOptions, option => option.value))} className="min-h-44 w-full border border-gray-300 bg-white p-2 text-sm">
        {selectableItems.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku} · {item.storage_zone ?? 'No zone'}</option>)}
      </select>
      <div className="flex items-center justify-between"><span className="text-xs text-gray-500">Select multiple items with Shift/Cmd. Default approval is AED 100 or 10%.</span><Button onClick={() => void createTemplate()} loading={savingTemplate} disabled={!branchId || selectedItems.length === 0}>Create template</Button></div>
      {message && <p className={`p-2 text-sm ${message.error ? 'bg-red-50 text-red-800' : 'bg-green-50 text-green-800'}`}>{message.text}</p>}
      {branchId && loadError && (
        <LoadError
          message="This branch's report templates could not be loaded. It may already have some — do not create a new one until this clears."
          onRetry={() => void reload()}
        />
      )}
      {branchId && !loadError && templates.length === 0 && <p className="border border-dashed border-gray-300 p-3 text-sm text-gray-500">No templates for this branch yet. Create the first version above.</p>}
      {templates.length > 0 && <DataTable rows={templates} rowKey={(row) => row.id} columns={[
        { header: 'Template', priority: 'primary', sortable: true, sortAccessor: (row) => row.name, render: (row) => row.name },
        { header: 'Type', sortable: true, sortAccessor: (row) => row.report_type, render: (row) => row.report_type.replaceAll('_', ' ') },
        { header: 'Cadence', sortable: true, sortAccessor: (row) => row.cadence, render: (row) => row.cadence.replaceAll('_', ' ') },
        { header: 'Fill order', sortable: true, sortAccessor: (row) => row.display_order, render: (row) => row.display_order },
        { header: 'Version', sortable: true, sortAccessor: (row) => row.version_number, render: (row) => `v${row.version_number}` },
        { header: 'Items', sortable: true, sortAccessor: (row) => row.items.length, render: (row) => row.items.length },
        { header: 'POS status', sortable: true, sortAccessor: (row) => latestTemplateIds.has(row.id) ? row.is_active ? 'Current' : 'Deactivated' : 'Superseded', render: (row) => latestTemplateIds.has(row.id) ? row.is_active ? <Badge variant="success">Current</Badge> : <Badge variant="neutral">Deactivated</Badge> : <Badge variant="neutral">Superseded</Badge> },
        { header: 'Required', sortable: true, sortAccessor: (row) => row.is_required ? 'Required' : 'Optional', render: (row) => row.is_required ? <Badge variant="warning">Required</Badge> : 'Optional' },
        { header: 'Action', render: (row) => latestTemplateIds.has(row.id) && row.is_active ? <Button size="sm" variant="outline" disabled={savingTemplate} onClick={() => void deactivateTemplate(row)}>Deactivate</Button> : '—' },
      ]} />}
    </div>
    <p className="text-xs text-gray-500">Submitted reports are reviewed and approved under the <strong>Report submissions</strong> tab.</p>
  </div>;
}
