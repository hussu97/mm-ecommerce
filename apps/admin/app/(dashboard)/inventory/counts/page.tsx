'use client';

import { useEffect, useRef, useState } from 'react';
import { inventoryApi, type StockAuditPreview } from '@/lib/pos-api';
import type { InventoryLevel } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { csvCell, formatQuantity } from '@/lib/utils';
import { BranchFilter, LedgerTab } from '../_shared';

export default function CountsPage() {
  const [branchId, setBranchId] = useState('');
  const [preview, setPreview] = useState<StockAuditPreview | null>(null);
  const [levels, setLevels] = useState<InventoryLevel[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const auditAttemptKey = useRef<string | null>(null);

  useEffect(() => {
    setPreview(null);
    auditAttemptKey.current = null;
    setMessage('');
    if (!branchId) {
      setLevels([]);
      return;
    }
    void inventoryApi.levels({ branch_id: branchId }).then(setLevels).catch(() => setLevels([]));
  }, [branchId]);

  const downloadTemplate = () => {
    const rows = [
      ['SKU', 'Item name', 'Unit', 'Expected quantity', 'Counted quantity', 'Remark'],
      ...levels.map((level) => [
        level.item_sku,
        level.item_name,
        'ingredient',
        formatQuantity(level.quantity),
        '',
        '',
      ]),
    ];
    const body = rows.map((row) => row.map(csvCell).join(',')).join('\n');
    const href = URL.createObjectURL(new Blob([body], { type: 'text/csv;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = `inventory-count-${branchId}.csv`;
    anchor.click();
    URL.revokeObjectURL(href);
  };

  const upload = async (file: File) => {
    if (!branchId) return;
    setBusy(true);
    try {
      setPreview(await inventoryApi.previewStockAuditFile(branchId, file));
      auditAttemptKey.current = null;
      setMessage('Review every delta below. Nothing has posted yet.');
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not read count sheet.');
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview?.valid || !branchId) return;
    setBusy(true);
    try {
      auditAttemptKey.current ??= `admin-stock-audit:${crypto.randomUUID()}`;
      const result = await inventoryApi.applyStockAudit({
        branch_id: branchId,
        idempotency_key: auditAttemptKey.current,
        rows: preview.rows.map((row) => ({
          sku: row.sku,
          counted_quantity: row.counted_quantity,
          unit: row.unit as 'storage' | 'ingredient',
          remark: row.remark,
        })),
      });
      setPreview(result);
      setMessage(`Stock audit posted${result.transaction_id ? ` as ${result.transaction_id}` : ''}.`);
      setLevels(await inventoryApi.levels({ branch_id: branchId }));
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not post stock audit.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-[1400px] space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <BranchFilter value={branchId} onChange={setBranchId} />
        <Button variant="outline" onClick={downloadTemplate} disabled={!branchId || levels.length === 0}>
          Download CSV template
        </Button>
        <label className="inline-flex min-h-10 cursor-pointer items-center border border-primary px-4 text-sm text-primary hover:bg-primary/5 aria-disabled:cursor-not-allowed">
          {busy ? 'Reading…' : 'Preview CSV / XLSX'}
          <input
            className="sr-only"
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            disabled={!branchId || busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
              event.target.value = '';
            }}
          />
        </label>
      </div>
      <p className="text-sm text-gray-500">
        Upload a fresh physical count. Preview validates duplicate or unknown SKUs, units and precision; applying posts only physical minus current ledger quantity.
      </p>
      {message && <div className="border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">{message}</div>}
      {preview && (
        <>
          <DataTable rows={preview.rows} rowKey={(row) => `${row.sku}-${row.counted_quantity}-${row.errors.join('|')}`} columns={[
            { header: 'SKU', priority: 'secondary', render: (row) => <code className="text-xs">{row.sku}</code> },
            { header: 'Item', priority: 'primary', render: (row) => row.item_name ?? 'Unknown item' },
            { header: 'Expected', className: 'text-right', render: (row) => formatQuantity(row.expected_quantity) },
            { header: 'Counted', className: 'text-right', render: (row) => formatQuantity(row.counted_quantity) },
            { header: 'Delta', className: 'text-right', render: (row) => <span className={Number(row.delta_quantity) === 0 ? 'text-gray-500' : Number(row.delta_quantity) < 0 ? 'text-red-700' : 'text-green-700'}>{formatQuantity(row.delta_quantity)} {row.unit}</span> },
            { header: 'Validation', render: (row) => row.errors.length ? <span className="text-red-700">{row.errors.join('; ')}</span> : <Badge variant="success">Ready</Badge> },
            { header: 'Remark', render: (row) => row.remark ?? '—' },
          ]} />
          <div className="flex justify-end">
            <Button onClick={() => void apply()} loading={busy} disabled={!preview.valid || Boolean(preview.transaction_id)}>
              {preview.transaction_id ? 'Audit posted' : 'Post count deltas'}
            </Button>
          </div>
        </>
      )}
      <div className="border-t border-gray-200 pt-5">
        <h3 className="mb-3 font-medium text-gray-800">Posted count history</h3>
        <LedgerTab countOnly />
      </div>
    </div>
  );
}
