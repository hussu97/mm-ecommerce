'use client';

import { useState } from 'react';
import type { BatchGroup } from '@/lib/types';


export function GroupRow({
  group,
  busy,
  onSave,
}: {
  group: BatchGroup;
  busy: boolean;
  onSave: (minutes: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [minutes, setMinutes] = useState(group.delivery_minutes_after_dispatch);

  return (
    <tr className={group.is_active ? '' : 'opacity-50'}>
      <td className="px-3 py-2.5 text-xs font-body text-gray-800">
        {group.name}
        {!group.is_active && <span className="text-gray-400"> · paused</span>}
      </td>
      <td className="px-3 py-2.5 text-xs font-body text-gray-400 max-w-xs truncate">
        {group.zone_names.join(' · ') || 'No zones yet'}
      </td>
      <td className="px-3 py-2.5 text-xs font-body text-gray-800">
        {editing ? (
          <input
            value={minutes}
            inputMode="numeric"
            onChange={e => setMinutes(Math.max(1, Math.min(1440, Number(e.target.value) || 1)))}
            className="w-16 px-2 py-1 text-xs font-body text-center bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
          />
        ) : (
          `${group.delivery_minutes_after_dispatch} minutes`
        )}
      </td>
      <td className="px-3 py-2.5 text-right whitespace-nowrap">
        {editing ? (
          <>
            <button
              onClick={() => onSave(minutes)}
              disabled={busy}
              className="text-[11px] font-body text-primary hover:underline mr-3"
            >
              Save
            </button>
            <button
              onClick={() => {
                setMinutes(group.delivery_minutes_after_dispatch);
                setEditing(false);
              }}
              disabled={busy}
              className="text-[11px] font-body text-gray-400 hover:underline"
            >
              Cancel
            </button>
          </>
        ) : (
          <button
            onClick={() => setEditing(true)}
            disabled={busy}
            className="text-[11px] font-body text-primary hover:underline"
          >
            Edit
          </button>
        )}
      </td>
    </tr>
  );
}
