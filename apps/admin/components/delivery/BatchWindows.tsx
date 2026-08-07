'use client';

import { useCallback, useEffect, useState } from 'react';
import { deliveryZonesApi, ApiError } from '@/lib/api';
import type { BatchWindow, BatchWindowWrite } from '@/lib/types';
import { Button, Spinner } from '@/components/ui';

/**
 * When a group's zones travel together.
 *
 * Every time here is Dubai time, written and read the same way — no offsets,
 * no conversion. An order packed inside a window waits for the window to close
 * and then leaves with everything else that collected in it; an order packed
 * in a gap goes out on its own, immediately.
 */

const PAD = (n: number) => String(n).padStart(2, '0');

function label(w: Pick<BatchWindow, 'start_hour' | 'start_minute' | 'end_hour' | 'end_minute'>) {
  return `${PAD(w.start_hour)}:${PAD(w.start_minute)} – ${PAD(w.end_hour)}:${PAD(w.end_minute)}`;
}

function blank(index: number): BatchWindowWrite {
  return {
    label: `Batch ${index + 1}`,
    start_hour: 0,
    start_minute: 0,
    end_hour: 1,
    end_minute: 0,
    is_active: true,
  };
}

interface Props {
  groupId: string;
  zoneName: string;
}

export function BatchWindows({ groupId, zoneName }: Props) {
  const [windows, setWindows] = useState<BatchWindow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<BatchWindowWrite | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setWindows(await deliveryZonesApi.listWindows(groupId));
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the schedule.');
    } finally {
      setLoading(false);
    }
  }, [groupId]);

  useEffect(() => { load(); }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await action();
      await load();
      setDraft(null);
    } catch (err) {
      // Overlaps come back as a 409 with a sentence explaining which two slots
      // collided — worth showing verbatim rather than replacing with "failed".
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="py-6 flex justify-center"><Spinner /></div>;
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">
          Batch schedule — {zoneName}
        </p>
        <p className="text-[11px] font-body text-gray-400 mt-1">
          Dubai time. An order packed inside a slot leaves when the slot closes;
          one packed outside every slot goes on its own straight away.
        </p>
      </div>

      {error && (
        <p className="px-3 py-2 text-xs font-body text-red-700 bg-red-50 border border-red-200">
          {error}
        </p>
      )}

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50">
            <th className="px-3 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Slot</th>
            <th className="px-3 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Collects</th>
            <th className="px-3 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Leaves</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {windows.map(w => (
            <WindowRow
              key={`${w.id}:${w.start_hour}:${w.start_minute}:${w.end_hour}:${w.end_minute}:${w.is_active}`}
              window={w}
              busy={busy}
              onSave={data => run(() => deliveryZonesApi.updateWindow(w.id, data))}
              onDelete={() =>
                confirm(`Remove "${w.label}"? Orders waiting on it are rescheduled.`) &&
                run(() => deliveryZonesApi.deleteWindow(w.id))
              }
            />
          ))}
          {!windows.length && !draft && (
            <tr>
              <td colSpan={4} className="px-3 py-4 text-xs font-body text-gray-400">
                No schedule. Every order in these zones is dispatched on its own,
                at full single-run cost.
              </td>
            </tr>
          )}
          {draft && (
            <WindowRow
              editing
              window={{ ...draft, id: 'new', group_id: groupId, wraps_midnight: false }}
              busy={busy}
              onSave={data => run(() => deliveryZonesApi.createWindow(groupId, data))}
              onDelete={() => setDraft(null)}
            />
          )}
        </tbody>
      </table>

      {!draft && (
        <Button size="sm" variant="ghost" onClick={() => setDraft(blank(windows.length))} disabled={busy}>
          <span className="material-icons text-[14px]">add</span>
          Add a slot
        </Button>
      )}
    </div>
  );
}

function WindowRow({
  window: initial,
  busy,
  editing: startEditing = false,
  onSave,
  onDelete,
}: {
  window: BatchWindow;
  busy: boolean;
  editing?: boolean;
  onSave: (data: BatchWindowWrite) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(startEditing);
  const [form, setForm] = useState<BatchWindowWrite>({
    label: initial.label,
    start_hour: initial.start_hour,
    start_minute: initial.start_minute,
    end_hour: initial.end_hour,
    end_minute: initial.end_minute,
    is_active: initial.is_active,
  });

  if (!editing) {
    return (
      <tr className={initial.is_active ? '' : 'opacity-50'}>
        <td className="px-3 py-2.5 text-xs font-body text-gray-800">
          {initial.label}
          {!initial.is_active && <span className="text-gray-400"> · paused</span>}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-600">
          {label(initial)}
          {initial.wraps_midnight && (
            <span className="text-gray-400"> (over midnight)</span>
          )}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-600">
          {PAD(initial.end_hour % 24)}:{PAD(initial.end_minute)}
        </td>
        <td className="px-3 py-2.5 text-right whitespace-nowrap">
          <button
            onClick={() => setEditing(true)}
            disabled={busy}
            className="text-[11px] font-body text-primary hover:underline mr-3"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            disabled={busy}
            className="text-[11px] font-body text-red-500 hover:underline"
          >
            Remove
          </button>
        </td>
      </tr>
    );
  }

  return (
    <tr className="bg-gray-50">
      <td className="px-3 py-2.5">
        <input
          value={form.label}
          onChange={e => setForm({ ...form, label: e.target.value })}
          className="w-28 px-2 py-1 text-xs font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
        />
      </td>
      <td className="px-3 py-2.5">
        <TimeField
          hour={form.start_hour}
          minute={form.start_minute}
          maxHour={23}
          onChange={(h, m) => setForm({ ...form, start_hour: h, start_minute: m })}
        />
      </td>
      <td className="px-3 py-2.5">
        <TimeField
          hour={form.end_hour}
          minute={form.end_minute}
          // 24:00 is midnight closing the day, so the last slot of the night
          // can be written without pretending it runs into tomorrow.
          maxHour={24}
          onChange={(h, m) => setForm({ ...form, end_hour: h, end_minute: m })}
        />
      </td>
      <td className="px-3 py-2.5 text-right whitespace-nowrap">
        <label className="inline-flex items-center gap-1 mr-3 text-[11px] font-body text-gray-500">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={e => setForm({ ...form, is_active: e.target.checked })}
          />
          Active
        </label>
        <button
          onClick={() => onSave(form)}
          disabled={busy}
          className="text-[11px] font-body text-primary hover:underline mr-3"
        >
          Save
        </button>
        <button
          onClick={() => (startEditing ? onDelete() : setEditing(false))}
          disabled={busy}
          className="text-[11px] font-body text-gray-400 hover:underline"
        >
          Cancel
        </button>
      </td>
    </tr>
  );
}

function TimeField({
  hour,
  minute,
  maxHour,
  onChange,
}: {
  hour: number;
  minute: number;
  maxHour: number;
  onChange: (hour: number, minute: number) => void;
}) {
  const clamp = (value: string, max: number) =>
    Math.max(0, Math.min(max, Number(value) || 0));
  const cell =
    'w-12 px-2 py-1 text-xs font-body text-center bg-white border border-gray-300 rounded-sm outline-none focus:border-primary';
  return (
    <span className="inline-flex items-center gap-1">
      <input
        value={PAD(hour)}
        inputMode="numeric"
        onChange={e => onChange(clamp(e.target.value, maxHour), minute)}
        className={cell}
      />
      <span className="text-xs text-gray-400">:</span>
      <input
        value={PAD(minute)}
        inputMode="numeric"
        onChange={e => onChange(hour, clamp(e.target.value, 59))}
        className={cell}
      />
    </span>
  );
}
