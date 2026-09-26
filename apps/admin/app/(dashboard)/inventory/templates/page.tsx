'use client';

// The report-TEMPLATE configurator: one template per report type per branch,
// which the register turns into the count sheet at close. Laid out like the
// transfer-template configurator (transfers/page.tsx) — a form above, the
// branch's templates below, Edit on a row loads it into the form — and a save
// on an edit changes that template in place (PUT); only "Create template" adds
// a new version, which replaces the type's current one.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { inventoryApi, type ReportTemplate, type ReportTemplateWrite } from '@/lib/pos-api';
import type { InventoryCategory, InventoryItem } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, LoadError, Select } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';
import { formatDateTime } from '@/lib/utils';
import {
  BranchFilter,
  GroupedItemSelect,
  REPORT_FILL_ORDER,
  REPORT_TEMPLATE_GUIDANCE,
  groupItemsByCategory,
  type ReportTemplateKind,
} from '../_shared';

const REPORT_TYPE_OPTIONS: { value: ReportTemplateKind; label: string }[] = [
  { value: 'production', label: 'Production' },
  { value: 'finished_goods', label: 'Finished goods' },
  { value: 'raw_materials', label: 'Raw materials' },
  { value: 'packaging', label: 'Packaging' },
  { value: 'spot_check', label: 'Spot check' },
];
const typeLabel = (type: string) => REPORT_TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type.replaceAll('_', ' ');

type Cadence = ReportTemplateWrite['cadence'];

// What a new template starts with. An edit keeps the template's own values.
const NEW_TEMPLATE_SETTINGS = {
  configuration: { visible_columns: ['opening', 'movements', 'expected', 'physical', 'variance', 'remark'] },
  approval_cost_threshold: '100',
  approval_variance_percent: '10',
};

export default function ReportTemplatesPage() {
  const [branchId, setBranchId] = useState('');
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [categories, setCategories] = useState<InventoryCategory[]>([]);
  // Whether the templates fetch failed. Without this an errored load is
  // indistinguishable from a branch with no templates, and the "create the
  // first one" prompt below invited a duplicate on top of a 500 (F-ADM-8).
  const [loadError, setLoadError] = useState(false);
  const [editing, setEditing] = useState<ReportTemplate | null>(null);
  const [name, setName] = useState(REPORT_TEMPLATE_GUIDANCE.finished_goods.defaultName);
  const [reportType, setReportType] = useState<ReportTemplateKind>('finished_goods');
  const [cadence, setCadence] = useState<Cadence>(REPORT_TEMPLATE_GUIDANCE.finished_goods.cadence);
  const [displayOrder, setDisplayOrder] = useState<number>(REPORT_FILL_ORDER.finished_goods);
  const [required, setRequired] = useState(true);
  const [isActive, setIsActive] = useState(true);
  const [selectedItems, setSelectedItems] = useState<string[]>([]);
  const [itemSearch, setItemSearch] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  const guidance = REPORT_TEMPLATE_GUIDANCE[reportType];
  const activeItems = useMemo(() => items.filter((item) => item.is_active && !item.deleted_at), [items]);
  const suggestedItems = useMemo(
    () => activeItems.filter((item) => guidance.kinds.includes(item.kind)),
    [activeItems, guidance.kinds],
  );
  // The picker offers the kinds this report counts, plus anything already on
  // the template, so an edit never hides a line it carries.
  const groupedItems = useMemo(() => {
    const search = itemSearch.trim().toLocaleLowerCase();
    const selected = new Set(selectedItems);
    const candidates = guidance.kinds.length > 0
      ? activeItems.filter((item) => guidance.kinds.includes(item.kind) || selected.has(item.id))
      : activeItems;
    return groupItemsByCategory(
      candidates.filter((item) => !search || `${item.name} ${item.sku} ${item.storage_zone ?? ''}`.toLocaleLowerCase().includes(search)),
      categories,
    );
  }, [activeItems, categories, guidance.kinds, itemSearch, selectedItems]);

  const reload = useCallback(async () => {
    if (!branchId) { setTemplates([]); setLoadError(false); return; }
    setLoadError(false);
    try {
      setTemplates(await inventoryApi.reportTemplates(branchId));
    } catch {
      setTemplates([]);
      setLoadError(true);
    }
  }, [branchId]);

  useEffect(() => {
    void inventoryApi.items().then(setItems).catch(() => setItems([]));
    void inventoryApi.categories().then(setCategories).catch(() => setCategories([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!branchId) { setTemplates([]); setLoadError(false); return () => { cancelled = true; }; }
    setLoadError(false);
    inventoryApi.reportTemplates(branchId)
      .then((rows) => { if (!cancelled) setTemplates(rows); })
      .catch(() => { if (!cancelled) { setTemplates([]); setLoadError(true); } });
    return () => { cancelled = true; };
  }, [branchId]);

  // The one template per report type the register uses: the highest version.
  const currentByType = useMemo(() => {
    const latest = new Map<string, ReportTemplate>();
    for (const template of templates) {
      const current = latest.get(template.report_type);
      if (!current || template.version_number > current.version_number) latest.set(template.report_type, template);
    }
    return latest;
  }, [templates]);
  const isCurrent = useCallback((row: ReportTemplate) => currentByType.get(row.report_type)?.id === row.id, [currentByType]);
  const tableRows = useMemo(
    () => (showHistory ? templates : templates.filter(isCurrent))
      .slice()
      .sort((a, b) => a.display_order - b.display_order || a.report_type.localeCompare(b.report_type) || b.version_number - a.version_number),
    [isCurrent, showHistory, templates],
  );
  const replacedCount = templates.length - currentByType.size;

  const startCreate = useCallback((type: ReportTemplateKind = 'finished_goods') => {
    const g = REPORT_TEMPLATE_GUIDANCE[type];
    setEditing(null);
    setReportType(type);
    setName(g.defaultName);
    setCadence(g.cadence);
    setDisplayOrder(REPORT_FILL_ORDER[type]);
    setRequired(g.required);
    setIsActive(true);
    setSelectedItems([]);
    setItemSearch('');
  }, []);

  // Switching branch abandons any in-progress edit — a template belongs to its
  // branch, so its item list makes no sense under another.
  useEffect(() => { startCreate(); setMessage(null); }, [branchId, startCreate]);

  const startEdit = (template: ReportTemplate) => {
    const activeIds = new Set(activeItems.map((item) => item.id));
    const lines = [...template.items].sort((a, b) => a.display_order - b.display_order);
    const kept = lines.filter((line) => activeIds.has(line.item_id)).map((line) => line.item_id);
    const dropped = lines.length - kept.length;
    setEditing(template);
    setReportType(template.report_type as ReportTemplateKind);
    setName(template.name);
    setCadence(template.cadence as Cadence);
    setDisplayOrder(template.display_order);
    setRequired(template.is_required);
    setIsActive(template.is_active);
    setSelectedItems(kept);
    setItemSearch('');
    setMessage(dropped > 0
      ? { text: `${dropped} item${dropped === 1 ? ' is' : 's are'} no longer active and will be removed from this template when you save.`, error: false }
      : null);
  };

  const save = async () => {
    if (!branchId || selectedItems.length === 0 || !name.trim()) return;
    setSaving(true);
    setMessage(null);
    // Lines go to the register in the picker's order — category, then name —
    // whatever order they were clicked in.
    const selected = new Set(selectedItems);
    const ordered = groupItemsByCategory(activeItems.filter((item) => selected.has(item.id)), categories)
      .flatMap((group) => group.items.map((item) => item.id));
    const inputs = new Map(editing?.items.map((line) => [line.item_id, line.required_input]) ?? []);
    const body: ReportTemplateWrite = {
      branch_id: branchId,
      name: name.trim(),
      report_type: reportType,
      cadence,
      is_required: required,
      is_active: isActive,
      display_order: displayOrder,
      configuration: editing ? editing.configuration : NEW_TEMPLATE_SETTINGS.configuration,
      approval_cost_threshold: editing ? editing.approval_cost_threshold : NEW_TEMPLATE_SETTINGS.approval_cost_threshold,
      approval_variance_percent: editing ? editing.approval_variance_percent : NEW_TEMPLATE_SETTINGS.approval_variance_percent,
      items: ordered.map((itemId, index) => ({
        item_id: itemId,
        display_order: index,
        required_input: (inputs.get(itemId) ?? guidance.requiredInput) as ReportTemplateWrite['items'][number]['required_input'],
      })),
    };
    try {
      if (editing) await inventoryApi.updateReportTemplate(editing.id, body);
      else await inventoryApi.createReportTemplate(body);
      setMessage({
        text: editing
          ? 'Template saved. The next report uses it — a report already raised keeps the list it was raised with.'
          : 'Template created. It will appear in the next matching POS checklist.',
        error: false,
      });
      startCreate(reportType);
      await reload();
    } catch (error) {
      setMessage({ text: error instanceof ApiError ? error.message : 'Could not save the template. Please try again.', error: true });
    } finally {
      setSaving(false);
    }
  };

  const applySuggestion = () => {
    if (!editing) {
      setName(guidance.defaultName);
      setCadence(guidance.cadence);
      setDisplayOrder(REPORT_FILL_ORDER[reportType]);
      setRequired(guidance.required);
    }
    setSelectedItems(suggestedItems.map((item) => item.id));
    setMessage(null);
  };

  const deactivate = async (template: ReportTemplate) => {
    setSaving(true);
    setMessage(null);
    try {
      await inventoryApi.deactivateReportTemplate(template.id);
      if (editing?.id === template.id) startCreate();
      setMessage({ text: `${template.name} is deactivated. POS will no longer create this report.`, error: false });
      await reload();
    } catch (error) {
      setMessage({ text: error instanceof ApiError ? error.message : 'Could not deactivate the template. Please try again.', error: true });
    } finally {
      setSaving(false);
    }
  };

  // Creating a template for a type that already has one replaces it, which is
  // rarely what someone who means to change it wants — say so, and offer Edit.
  const existingForType = !editing ? currentByType.get(reportType) : undefined;

  return <div className="space-y-5">
    <BranchFilter value={branchId} onChange={setBranchId} />
    <p className="text-sm text-gray-500">Choose a branch first: its templates own their own item list. POS uses one template per report type for that branch. Outstanding reports remain visible after the till closes.</p>
    <div className="border border-gray-200 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-medium text-gray-800">{editing ? `Editing ${editing.name}` : 'Branch report templates'}</h3>
        <Badge>{currentByType.size} template{currentByType.size === 1 ? '' : 's'}</Badge>
      </div>
      {!branchId ? (
        <p className="border border-dashed border-gray-300 p-3 text-sm text-gray-500">Choose a branch above to configure its report templates.</p>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-4">
            <Input label="Template name" value={name} onChange={(event) => setName(event.target.value)} />
            <Select
              label="Type"
              value={reportType}
              disabled={!!editing}
              title={editing ? 'A template keeps its report type — create a new template for another type.' : undefined}
              onChange={(event) => { startCreate(event.target.value as ReportTemplateKind); setMessage(null); }}
              options={REPORT_TYPE_OPTIONS}
            />
            <Select label="Cadence" value={cadence} onChange={(event) => setCadence(event.target.value as Cadence)} options={[
              { value: 'per_till', label: 'At till close (per till)' }, { value: 'per_business_day', label: 'At end of day (per business day)' }, { value: 'ad_hoc', label: 'Ad hoc (manual only)' },
            ]} />
            <Input label="Fill order (low first)" type="number" value={String(displayOrder)} onChange={(event) => setDisplayOrder(Number(event.target.value) || 0)} />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={required} onChange={(event) => setRequired(event.target.checked)} />Required (may be deferred/waived)</label>
            {editing && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} />Active</label>}
          </div>
          {existingForType && (
            <div className="flex flex-wrap items-center justify-between gap-2 border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
              <span>This branch already has a {typeLabel(reportType)} template, <strong>{existingForType.name}</strong>. Creating another replaces it — to change it, edit it instead.</span>
              <Button type="button" size="sm" variant="outline" className="bg-white" onClick={() => startEdit(existingForType)}>Edit {existingForType.name}</Button>
            </div>
          )}
          <div className="border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
            <p className="font-medium">What staff will do</p>
            <p className="mt-1">{guidance.staffInstruction}</p>
            <Button type="button" variant="outline" size="sm" className="mt-3 bg-white" onClick={applySuggestion} disabled={guidance.kinds.length === 0}>
              Use suggested {guidance.kinds.length > 0 ? `${suggestedItems.length}-item set` : 'item set'}
            </Button>
          </div>
          <div className="flex flex-wrap items-end justify-between gap-2">
            <label className="block flex-1 text-xs uppercase tracking-wider text-gray-500">Items in this template
              <Input aria-label="Search report-template items" value={itemSearch} onChange={(event) => setItemSearch(event.target.value)} placeholder="Search name, SKU or storage zone" className="mt-1" />
            </label>
            <span className="pb-2 text-xs text-gray-500">{selectedItems.length} selected · grouped by category</span>
          </div>
          <GroupedItemSelect label="Report-template items" groups={groupedItems} value={selectedItems} onChange={setSelectedItems} />
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-500">Select multiple items with Shift/Cmd.{!editing && ' Default approval is AED 100 or 10%.'}</span>
            <div className="flex gap-2">
              {editing && <Button variant="outline" onClick={() => { startCreate(); setMessage(null); }} disabled={saving}>Cancel edit</Button>}
              <Button onClick={() => void save()} loading={saving} disabled={selectedItems.length === 0 || !name.trim()}>{editing ? 'Save changes' : 'Create template'}</Button>
            </div>
          </div>
          {message && <p className={`p-2 text-sm ${message.error ? 'bg-red-50 text-red-800' : 'bg-green-50 text-green-800'}`}>{message.text}</p>}
          {loadError && (
            <LoadError
              message="This branch's report templates could not be loaded. It may already have some — do not create a new one until this clears."
              onRetry={() => void reload()}
            />
          )}
          {!loadError && templates.length === 0 && <p className="border border-dashed border-gray-300 p-3 text-sm text-gray-500">No templates for this branch yet. Create the first one above.</p>}
          {templates.length > 0 && <>
            {replacedCount > 0 && (
              <label className="flex items-center gap-2 text-xs text-gray-500">
                <input type="checkbox" checked={showHistory} onChange={(event) => setShowHistory(event.target.checked)} />
                Show {replacedCount} replaced version{replacedCount === 1 ? '' : 's'}
              </label>
            )}
            <DataTable rows={tableRows} rowKey={(row) => row.id} columns={[
              { header: 'Template', priority: 'primary', sortable: true, sortAccessor: (row) => row.name, render: (row) => row.name },
              { header: 'Type', sortable: true, sortAccessor: (row) => row.report_type, render: (row) => typeLabel(row.report_type) },
              { header: 'Cadence', sortable: true, sortAccessor: (row) => row.cadence, render: (row) => row.cadence.replaceAll('_', ' ') },
              { header: 'Fill order', sortable: true, sortAccessor: (row) => row.display_order, render: (row) => row.display_order },
              { header: 'Items', sortable: true, sortAccessor: (row) => row.items.length, render: (row) => row.items.length },
              { header: 'Last edited', sortable: true, sortAccessor: (row) => row.updated_at, render: (row) => formatDateTime(row.updated_at) },
              ...(showHistory ? [{ header: 'Version', sortable: true, sortAccessor: (row: ReportTemplate) => row.version_number, render: (row: ReportTemplate) => `v${row.version_number}` }] : []),
              { header: 'POS status', sortable: true, sortAccessor: (row) => isCurrent(row) ? row.is_active ? 'Current' : 'Deactivated' : 'Replaced', render: (row) => isCurrent(row) ? row.is_active ? <Badge variant="success">Current</Badge> : <Badge variant="neutral">Deactivated</Badge> : <Badge variant="neutral">Replaced</Badge> },
              { header: 'Required', sortable: true, sortAccessor: (row) => row.is_required ? 'Required' : 'Optional', render: (row) => row.is_required ? <Badge variant="warning">Required</Badge> : 'Optional' },
            ]} actions={(row) => isCurrent(row) ? (
              <>
                <RowAction onClick={() => startEdit(row)}>Edit</RowAction>
                {row.is_active && <RowAction disabled={saving} onClick={() => void deactivate(row)}>Deactivate</RowAction>}
              </>
            ) : null} />
          </>}
        </>
      )}
    </div>
    <p className="text-xs text-gray-500">Submitted reports are reviewed and approved under the <strong>Report submissions</strong> tab.</p>
  </div>;
}
