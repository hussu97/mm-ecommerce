'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { inventoryApi, type ReportSave, type ShiftInventoryReport } from '@/lib/pos-api';
import { ApiError } from '@/lib/api';
import { Badge, Button, Spinner } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { formatCurrency, formatDateTime, formatQuantity } from '@/lib/utils';

type ReportLine = ShiftInventoryReport['lines'][number];
type GridColumn = { key: string; label: string; role: string; source: string; posts: string | null; editable: boolean };

function statusVariant(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'posted' || status === 'approved') return 'success';
  if (status === 'pending_approval') return 'warning';
  if (status === 'rejected') return 'danger';
  return 'neutral';
}

const num = (value: unknown): number => Number(value ?? 0);
const lineValue = (line: ReportLine, key: string): string => String((line as Record<string, unknown>)[key] ?? '');
const itemName = (line: ReportLine): string => {
  const summary = (line.source_summary ?? {}) as Record<string, unknown>;
  return String(summary.item_name ?? line.item_id);
};
const itemCategory = (line: ReportLine): string => {
  const summary = (line.source_summary ?? {}) as Record<string, unknown>;
  return summary.category_name ? String(summary.category_name) : 'Uncategorised';
};

export default function ReportDetailPage() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const confirm = useConfirm();

  const [report, setReport] = useState<ShiftInventoryReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const [editing, setEditing] = useState(false);
  // itemId -> columnKey -> typed value; and the movement keys the reviewer changed.
  const [values, setValues] = useState<Record<string, Record<string, string>>>({});
  const [changed, setChanged] = useState<Record<string, Set<string>>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [commentBody, setCommentBody] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await inventoryApi.shiftReport(id));
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load the report.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const columns = useMemo<GridColumn[]>(
    () => ((report?.columns ?? []) as unknown as GridColumn[]),
    [report],
  );
  const isPending = report?.status === 'pending_approval';

  const beginEdit = () => {
    if (!report) return;
    const seed: Record<string, Record<string, string>> = {};
    const seedReasons: Record<string, string> = {};
    for (const line of report.lines) {
      const row: Record<string, string> = {};
      for (const col of columns) {
        if (col.editable) {
          const raw = lineValue(line, col.key);
          row[col.key] = raw ? formatQuantity(raw) : '';
        }
      }
      seed[line.item_id] = row;
      seedReasons[line.item_id] = line.override_reason ?? '';
    }
    setValues(seed);
    setChanged({});
    setReasons(seedReasons);
    setEditing(true);
  };

  const setCell = (itemId: string, key: string, value: string) => {
    setValues((prev) => ({ ...prev, [itemId]: { ...prev[itemId], [key]: value } }));
    if (key !== 'entered_quantity') {
      setChanged((prev) => {
        const next = new Set(prev[itemId] ?? []);
        next.add(key);
        return { ...prev, [itemId]: next };
      });
    }
  };

  // The current value of a column for a line: the reviewer's edit while editing,
  // otherwise the stored figure. Closing and Difference are derived from these so
  // the reviewer sees the effect of an edit before saving, exactly as POS does.
  const cellNumber = (line: ReportLine, key: string): number => {
    if (editing && values[line.item_id]?.[key] !== undefined) return num(values[line.item_id][key]);
    return num(lineValue(line, key));
  };
  const liveNet = (line: ReportLine): number => {
    let total = num(line.opening_quantity);
    for (const col of columns) {
      if (col.role === 'in') total += cellNumber(line, col.key);
      if (col.role === 'out') total -= cellNumber(line, col.key);
    }
    return total;
  };
  const cellDisplay = (line: ReportLine, col: GridColumn): string => {
    if (col.role === 'net') return formatQuantity(editing ? liveNet(line) : num(line.expected_quantity));
    if (col.role === 'difference') {
      if (editing) return formatQuantity(cellNumber(line, 'entered_quantity') - liveNet(line));
      return line.variance_quantity == null ? '—' : formatQuantity(num(line.variance_quantity));
    }
    const raw = lineValue(line, col.key);
    return raw ? formatQuantity(raw) : '0';
  };

  const save = async () => {
    if (!report) return;
    setBusy(true);
    try {
      const body: ReportSave = {
        idempotency_key: `admin:${crypto.randomUUID()}`,
        base_posting_sequence: report.base_posting_sequence ?? null,
        notes: report.notes ?? null,
        lines: report.lines.map((line) => {
          const row = values[line.item_id] ?? {};
          const movements: Record<string, number> = {};
          for (const key of changed[line.item_id] ?? []) movements[key] = num(row[key]);
          const enteredRaw = row.entered_quantity;
          const reason = (reasons[line.item_id] ?? '').trim();
          return {
            item_id: line.item_id,
            entered_quantity: enteredRaw === undefined || enteredRaw === '' ? null : String(num(enteredRaw)),
            confirmed: true,
            override_reason: reason || null,
            movements,
          };
        }),
      };
      const updated = await inventoryApi.editReport(report.id, body);
      setReport(updated);
      setEditing(false);
      toast.success('Report updated.');
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not save the report.');
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!report) return;
    if (!(await confirm({
      title: 'Approve report',
      message: 'Approving posts these counts to the stock ledger. Continue?',
      confirmLabel: 'Approve & post',
    }))) return;
    setBusy(true);
    try {
      setReport(await inventoryApi.approveReport(report.id));
      toast.success('Report approved and posted to the ledger.');
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not approve the report.');
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (!report) return;
    setBusy(true);
    try {
      setReport(await inventoryApi.rejectReport(report.id, rejectReason.trim()));
      setRejecting(false);
      setRejectReason('');
      toast.success('Report rejected. Nothing was posted to the ledger.');
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not reject the report.');
    } finally {
      setBusy(false);
    }
  };

  const addComment = async () => {
    if (!report) return;
    const body = commentBody.trim();
    if (!body) return;
    setBusy(true);
    try {
      setReport(await inventoryApi.addReportComment(report.id, body));
      setCommentBody('');
      toast.success('Comment added. The shop sees it on the till.');
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not add the comment.');
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="p-6"><Spinner /></div>;
  if (error || !report) return (
    <div className="p-6 space-y-3">
      <p className="bg-red-50 p-3 text-sm text-red-800">{error || 'Report not found.'}</p>
      <Link href="/inventory" className="text-sm text-primary underline">Back to inventory</Link>
    </div>
  );

  const reportName = String(report.template_snapshot?.name ?? report.template_id);
  const varianceTotal = report.lines.reduce((sum, line) => sum + num(line.variance_cost), 0);
  // Group lines by category, categories in display order, items by name — the same
  // order the register presents the count in.
  const grouped = (() => {
    const map = new Map<string, { order: number; lines: ReportLine[] }>();
    for (const line of report.lines) {
      const summary = (line.source_summary ?? {}) as Record<string, unknown>;
      const name = itemCategory(line);
      const order = summary.category_order == null ? Number.MAX_SAFE_INTEGER : Number(summary.category_order);
      const bucket = map.get(name) ?? { order, lines: [] };
      bucket.order = Math.min(bucket.order, order);
      bucket.lines.push(line);
      map.set(name, bucket);
    }
    return [...map.entries()]
      .sort((a, b) => a[1].order - b[1].order || a[0].localeCompare(b[0]))
      .map(([name, bucket]) => ({ name, lines: [...bucket.lines].sort((a, b) => itemName(a).localeCompare(itemName(b))) }));
  })();

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/inventory" className="text-xs text-gray-400 hover:text-primary">← Inventory</Link>
          <h1 className="font-display text-xl text-primary tracking-wide">{reportName}</h1>
        </div>
        <Badge variant={statusVariant(report.status)}>{report.status.replaceAll('_', ' ')}</Badge>
      </div>

      <div className="grid gap-3 border border-gray-200 p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <Detail label="Branch" value={report.branch_name ?? '—'} />
        <Detail label="Business date" value={report.business_date} />
        <Detail label="Submitted by" value={report.submitted_by_name ?? '—'} />
        <Detail label="Submitted at" value={report.submitted_at ? formatDateTime(report.submitted_at) : '—'} />
        <Detail label="Approved by" value={report.approved_by_name ?? '—'} />
        <Detail label="Approved at" value={report.approved_at ? formatDateTime(report.approved_at) : '—'} />
        <Detail label="Variance value" value={formatCurrency(varianceTotal)} />
        <Detail label="Progress" value={`${report.lines.filter((l) => l.confirmed).length}/${report.lines.length} confirmed`} />
        {report.notes && <Detail label="Notes" value={report.notes} />}
        {report.deferred_reason && <Detail label="Reason" value={report.deferred_reason} />}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {isPending && !editing && <Button variant="outline" onClick={beginEdit} disabled={busy}>Edit values</Button>}
        {editing && <Button onClick={() => void save()} loading={busy}>Save changes</Button>}
        {editing && <Button variant="ghost" onClick={() => setEditing(false)} disabled={busy}>Cancel</Button>}
        {isPending && !editing && <Button onClick={() => void approve()} loading={busy}>Approve &amp; post</Button>}
        {isPending && !editing && <Button variant="outline" onClick={() => setRejecting((v) => !v)} disabled={busy}>Reject</Button>}
      </div>

      {rejecting && (
        <div className="border border-red-200 bg-red-50 p-3 space-y-2">
          <label className="block text-xs uppercase tracking-wider text-red-800">Reason for rejection
            <textarea value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} rows={2} className="mt-1 w-full border border-red-300 bg-white p-2 text-sm" placeholder="What needs correcting before this count can be posted?" />
          </label>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => void reject()} loading={busy}>Confirm reject</Button>
            <Button variant="ghost" onClick={() => { setRejecting(false); setRejectReason(''); }} disabled={busy}>Cancel</Button>
          </div>
        </div>
      )}

      {editing && <p className="text-xs text-gray-500">Closing and Difference recalculate as you type; the ledger takes these figures when you approve.</p>}

      <div className="overflow-x-auto border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="px-2 py-1">Item</th>
              <th className="px-2 py-1">Unit</th>
              {columns.map((col) => <th key={col.key} className="px-2 py-1 text-right">{col.label}</th>)}
              <th className="px-2 py-1">Remark</th>
            </tr>
          </thead>
          <tbody>
            {grouped.map((group) => (
              <GroupRows key={group.name} name={group.name} span={columns.length + 3}>
                {group.lines.map((line) => (
                  <tr key={line.id} className="border-t border-gray-100">
                    <td className="px-2 py-1 font-medium">{itemName(line)}</td>
                    <td className="px-2 py-1 text-gray-500">{line.unit}</td>
                    {columns.map((col) => (
                      <td key={col.key} className="px-2 py-1 text-right">
                        {editing && col.editable ? (
                          <input
                            inputMode="decimal"
                            value={values[line.item_id]?.[col.key] ?? ''}
                            onChange={(e) => setCell(line.item_id, col.key, e.target.value)}
                            className="w-20 border border-gray-300 px-1 py-0.5 text-right"
                          />
                        ) : (
                          cellDisplay(line, col)
                        )}
                      </td>
                    ))}
                    <td className="px-2 py-1">
                      {editing ? (
                        <input
                          value={reasons[line.item_id] ?? ''}
                          onChange={(e) => setReasons((prev) => ({ ...prev, [line.item_id]: e.target.value }))}
                          className="w-40 border border-gray-300 px-1 py-0.5"
                          placeholder="Optional"
                        />
                      ) : (
                        <span className="text-gray-500">{line.override_reason ?? ''}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </GroupRows>
            ))}
          </tbody>
        </table>
      </div>

      <div className="border border-gray-200 p-4 space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-600">Comments</h2>
        <p className="text-xs text-gray-500">
          Notes to the shop about this count — why it was queried, what to recheck. They show on the register under POS actions ▸ Reports.
        </p>
        {report.comments.length === 0 ? (
          <p className="text-sm text-gray-400">No comments yet.</p>
        ) : (
          <ul className="space-y-3">
            {report.comments.map((comment) => (
              <li key={comment.id} className="border-l-2 border-primary/40 pl-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-medium text-gray-800">{comment.author_name ?? 'A reviewer'}</span>
                  <span className="text-xs text-gray-400">{formatDateTime(comment.created_at)}</span>
                </div>
                <p className="whitespace-pre-wrap text-sm text-gray-700">{comment.body}</p>
              </li>
            ))}
          </ul>
        )}
        <div className="space-y-2">
          <textarea
            value={commentBody}
            onChange={(e) => setCommentBody(e.target.value)}
            rows={2}
            className="w-full border border-gray-300 bg-white p-2 text-sm"
            placeholder="Add a note for the shop…"
          />
          <Button onClick={() => void addComment()} loading={busy} disabled={!commentBody.trim()}>Add comment</Button>
        </div>
      </div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wider text-gray-400">{label}</p>
      <p className="text-gray-800">{value}</p>
    </div>
  );
}

function GroupRows({ name, span, children }: { name: string; span: number; children: React.ReactNode }) {
  return (
    <>
      <tr className="bg-gray-100/70">
        <td colSpan={span} className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-gray-600">{name}</td>
      </tr>
      {children}
    </>
  );
}
