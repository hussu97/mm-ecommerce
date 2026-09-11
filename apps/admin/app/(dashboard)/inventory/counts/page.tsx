'use client';

import { Fragment, useEffect, useRef, useState } from 'react';
import { inventoryApi, type StockAuditPreview } from '@/lib/pos-api';
import type { InventoryLevel } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button } from '@/components/ui';
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
          <div className="overflow-x-auto border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
                <tr>
                  <th className="px-2 py-1">SKU</th>
                  <th className="px-2 py-1">Item</th>
                  <th className="px-2 py-1 text-right">Expected</th>
                  <th className="px-2 py-1 text-right">Counted</th>
                  <th className="px-2 py-1 text-right">Delta</th>
                  <th className="px-2 py-1">Validation</th>
                  <th className="px-2 py-1">Remark</th>
                </tr>
              </thead>
              <tbody>
                {(() => {
                  const MAX = Number.MAX_SAFE_INTEGER;
                  const buckets = new Map<string, { order: number; rows: typeof preview.rows }>();
                  for (const row of preview.rows) {
                    const name = row.category_name ?? 'Uncategorised';
                    const order = row.category_name ? (row.category_order ?? MAX) : MAX;
                    const bucket = buckets.get(name) ?? { order: MAX, rows: [] };
                    bucket.order = Math.min(bucket.order, order);
                    bucket.rows.push(row);
                    buckets.set(name, bucket);
                  }
                  const groups = [...buckets.entries()].sort(
                    (a, b) => a[1].order - b[1].order || a[0].localeCompare(b[0]),
                  );
                  for (const [, bucket] of groups) {
                    bucket.rows.sort((x, y) => (x.item_name ?? x.sku).localeCompare(y.item_name ?? y.sku));
                  }
                  return groups.map(([category, bucket]) => (
                    <Fragment key={category}>
                      <tr className="bg-gray-100/70">
                        <td colSpan={7} className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-gray-600">{category}</td>
                      </tr>
                      {bucket.rows.map((row) => (
                        <tr key={`${row.sku}-${row.counted_quantity}-${row.errors.join('|')}`} className="border-t border-gray-100">
                          <td className="px-2 py-1"><code className="text-xs">{row.sku}</code></td>
                          <td className="px-2 py-1 font-medium">{row.item_name ?? 'Unknown item'}</td>
                          <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(row.expected_quantity)}</td>
                          <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(row.counted_quantity)}</td>
                          <td className={`px-2 py-1 text-right tabular-nums ${Number(row.delta_quantity) === 0 ? 'text-gray-500' : Number(row.delta_quantity) < 0 ? 'text-red-700' : 'text-green-700'}`}>{formatQuantity(row.delta_quantity)} {row.unit}</td>
                          <td className="px-2 py-1">{row.errors.length ? <span className="text-red-700">{row.errors.join('; ')}</span> : <Badge variant="success">Ready</Badge>}</td>
                          <td className="px-2 py-1">{row.remark ?? '—'}</td>
                        </tr>
                      ))}
                    </Fragment>
                  ));
                })()}
              </tbody>
            </table>
          </div>
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
