'use client';

import { useState } from 'react';
import { COURIER_LABEL } from './courier-labels';
import type { Courier, CourierWrite } from '@/lib/types';


export function describe(courier: Courier): string {
  if (courier.unbatched_promise_kind === 'minutes') {
    return courier.unbatched_promise_minutes
      ? `${courier.unbatched_promise_minutes} minutes from ready`
      : '— not set';
  }
  const days = courier.unbatched_promise_days;
  return `${days} day${days === 1 ? '' : 's'} after handover`;
}

export function CourierRow({
  courier,
  busy,
  onSave,
}: {
  courier: Courier;
  busy: boolean;
  onSave: (data: CourierWrite) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<{
    unbatched_promise_kind: 'minutes' | 'next_day';
    // Never null in the form, unlike the column: the field always shows a
    // number to edit, and only the one the chosen kind reads is submitted.
    unbatched_promise_minutes: number;
    unbatched_promise_days: number;
  }>({
    unbatched_promise_kind: courier.unbatched_promise_kind,
    // Kept even while the other kind is selected, so switching back and forth
    // does not lose the number that was already there.
    unbatched_promise_minutes: courier.unbatched_promise_minutes ?? 60,
    unbatched_promise_days: courier.unbatched_promise_days,
  });

  if (!editing) {
    return (
      <tr className={courier.is_active ? '' : 'opacity-50'}>
        <td className="px-3 py-2.5 text-xs font-body text-gray-800">
          {COURIER_LABEL[courier.code] ?? courier.name}
          {!courier.is_active && <span className="text-gray-400"> · off</span>}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-500">
          {courier.zone_count || '—'}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-500">
          {courier.unbatched_promise_kind === 'minutes' ? 'A time' : 'A day'}
          {courier.supports_batching && (
            <span className="text-gray-400"> · can batch</span>
          )}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-800">
          {describe(courier)}
        </td>
        <td className="px-3 py-2.5 text-right">
          <button
            onClick={() => setEditing(true)}
            disabled={busy}
            className="text-[11px] font-body text-primary hover:underline"
          >
            Edit
          </button>
        </td>
      </tr>
    );
  }

  const isMinutes = form.unbatched_promise_kind === 'minutes';
  return (
    <tr className="bg-gray-50">
      <td className="px-3 py-2.5 text-xs font-body text-gray-800">
        {COURIER_LABEL[courier.code] ?? courier.name}
      </td>
      <td className="px-3 py-2.5 text-xs font-body text-gray-500">
        {courier.zone_count || '—'}
      </td>
      <td className="px-3 py-2.5">
        <select
          value={form.unbatched_promise_kind}
          onChange={e =>
            setForm({
              ...form,
              unbatched_promise_kind: e.target.value as 'minutes' | 'next_day',
            })
          }
          className="px-2 py-1 text-xs font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
        >
          <option value="minutes">A time</option>
          <option value="next_day">A day</option>
        </select>
      </td>
      <td className="px-3 py-2.5">
        <span className="inline-flex items-center gap-1.5">
          <input
            value={isMinutes ? form.unbatched_promise_minutes : form.unbatched_promise_days}
            inputMode="numeric"
            onChange={e => {
              const value = Math.max(1, Number(e.target.value) || 1);
              setForm(
                isMinutes
                  ? { ...form, unbatched_promise_minutes: Math.min(1440, value) }
                  : { ...form, unbatched_promise_days: Math.min(30, value) },
              );
            }}
            className="w-16 px-2 py-1 text-xs font-body text-center bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
          />
          <span className="text-[11px] font-body text-gray-500">
            {isMinutes ? 'minutes from ready' : 'days after handover'}
          </span>
        </span>
      </td>
      <td className="px-3 py-2.5 text-right whitespace-nowrap">
        <button
          onClick={() =>
            onSave(
              // Only the number the chosen kind actually reads is sent. Writing
              // both would put a plausible figure in the column nothing uses,
              // which is how a wrong number survives long enough to be quoted.
              isMinutes
                ? {
                    unbatched_promise_kind: 'minutes',
                    unbatched_promise_minutes: form.unbatched_promise_minutes,
                  }
                : {
                    unbatched_promise_kind: 'next_day',
                    unbatched_promise_days: form.unbatched_promise_days,
                  },
            )
          }
          disabled={busy}
          className="text-[11px] font-body text-primary hover:underline mr-3"
        >
          Save
        </button>
        <button
          onClick={() => setEditing(false)}
          disabled={busy}
          className="text-[11px] font-body text-gray-400 hover:underline"
        >
          Cancel
        </button>
      </td>
    </tr>
  );
}
