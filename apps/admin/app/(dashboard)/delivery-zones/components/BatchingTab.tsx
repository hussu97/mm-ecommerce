'use client';

import { useState, useEffect } from 'react';
import { deliveryZonesApi } from '@/lib/api';
import { BatchWindows } from '@/components/delivery/BatchWindows';
import { PROVIDER_LABEL } from './provider-labels';
import type { BatchGroup, FulfilmentProvider } from '@/lib/types';


// ── Batching ──────────────────────────────────────────────────────────────────

/**
 * The schedule, per group.
 *
 * A group is a set of zones whose orders ride together on one courier booking.
 * This screen used to list zones, each with its own schedule, and which of them
 * actually shared a van fell out of two schedules coincidentally ending on the
 * same minute — a decision nobody made and this page could not show. Listing
 * groups is the point: what you see here is what leaves together.
 *
 * A zone in no group is not missing a schedule. It dispatches the moment the
 * order is ready, which is the right answer for noon Send and for every third
 * party, and it is stated under each group rather than left as an absence.
 */
export function BatchingTab() {
  const [groups, setGroups] = useState<BatchGroup[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    deliveryZonesApi
      .listBatchGroups()
      .then(rows => {
        setGroups(rows);
        setOpen(rows[0]?.id ?? null);
      })
      .catch(err => setError((err as Error).message));
  }, []);

  if (error) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-red-600">
        {error}
      </div>
    );
  }
  if (groups === null) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-400">
        Loading schedules…
      </div>
    );
  }
  if (!groups.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        No batch groups. Every zone dispatches its orders the moment they are
        ready. Only a courier that can carry several of our orders in one
        booking can have a schedule at all.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {groups.map(group => (
        <div key={group.id} className="bg-white border border-gray-200">
          <button
            onClick={() => setOpen(open === group.id ? null : group.id)}
            className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
          >
            <span className="material-icons text-[18px] text-gray-400">
              {open === group.id ? 'expand_less' : 'expand_more'}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-body text-gray-800">
                {group.name}
              </span>
              {/* The zones on this van, spelled out. The whole reason the group
                  exists is that this list used to be unknowable. */}
              <span className="block text-xs font-body text-gray-400 truncate">
                {group.zone_names.join(' · ') || 'No zones on this schedule yet'}
              </span>
            </span>
            <span className="text-xs font-body text-gray-500 shrink-0">
              {PROVIDER_LABEL[group.courier_code as FulfilmentProvider] ??
                group.courier_code}{' '}
              ·{' '}
              {group.delivery_minutes_after_dispatch}m to the door
            </span>
          </button>
          {open === group.id && (
            <div className="border-t border-gray-100 px-4 py-3">
              <BatchWindows groupId={group.id} zoneName={group.name} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
