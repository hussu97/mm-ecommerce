'use client';

import { useCallback, useEffect, useState } from 'react';
import type { Schemas } from '@mm/types';
import { branchesApi } from '@/lib/pos-api';
import { ApiError } from '@/lib/api';
import type { Branch } from '@/lib/pos-types';
import { Button, Spinner } from '@/components/ui';
import { useConfirm } from '@/components/ui/feedback';

type BusyStatus = Schemas['BusyStatusResponse'];
type Chip = Schemas['BusyChannelResponse'];

/**
 * Busy mode — a branch offline for website and delivery-app orders.
 *
 * The console's copy of the register's switch. The register is where it is
 * normally thrown (the rush is at the counter); this is for a manager who is
 * not there. Same endpoint semantics: the website routes around the branch at
 * once, and each marketplace outlet is closed within seconds — one chip each.
 */

const CHIP_TEXT: Record<Chip['state'], string> = {
  closing: 'closing…',
  closed: 'offline',
  opening: 'reopening…',
  open: 'online',
  failed: 'failed',
  manual: 'close manually',
  not_live: 'writes off',
};

const CHIP_TONE: Record<Chip['state'], string> = {
  closing: 'border-amber-300 text-amber-800 bg-amber-50',
  opening: 'border-amber-300 text-amber-800 bg-amber-50',
  closed: 'border-gray-300 text-gray-700 bg-gray-50',
  open: 'border-green-300 text-green-800 bg-green-50',
  failed: 'border-red-300 text-red-800 bg-red-50',
  manual: 'border-red-300 text-red-800 bg-red-50',
  not_live: 'border-gray-300 text-gray-500 bg-white',
};

function since(iso: string | null | undefined): string {
  if (!iso) return '';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Dubai',
    hour: 'numeric',
    minute: '2-digit',
    day: 'numeric',
    month: 'short',
  }).format(new Date(iso));
}

function inFlight(status: BusyStatus | undefined): boolean {
  return !!status?.channels?.some((c) => c.state === 'closing' || c.state === 'opening');
}

export function BranchBusyMode({ branches }: { branches: Branch[] }) {
  const confirm = useConfirm();
  const [statuses, setStatuses] = useState<Record<string, BusyStatus>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const live = branches.filter((b) => !b.deleted_at && b.is_active && b.receives_online_orders);

  const refresh = useCallback(async () => {
    const pairs = await Promise.all(
      live.map(async (b) => [b.id, await branchesApi.busy(b.id).catch(() => undefined)] as const),
    );
    setStatuses(Object.fromEntries(pairs.filter(([, s]) => s !== undefined)) as Record<string, BusyStatus>);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.map((b) => b.id).join(',')]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Watch the chips land while a toggle is in flight; otherwise stay quiet.
  const watching = Object.values(statuses).some(inFlight);
  useEffect(() => {
    if (!watching) return;
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [watching, refresh]);

  const toggle = async (branch: Branch, busy: boolean) => {
    if (busy) {
      const ok = await confirm({
        title: `Take ${branch.name} offline?`,
        message:
          'The website stops sending it orders at once, and every delivery app it trades on is closed within seconds. It stays offline until busy mode is turned off.',
        confirmLabel: 'Turn on busy mode',
        danger: true,
      });
      if (!ok) return;
    }
    setSaving(branch.id);
    setError(null);
    try {
      const status = await branchesApi.setBusy(branch.id, busy);
      setStatuses((prev) => ({ ...prev, [branch.id]: status }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change busy mode');
    } finally {
      setSaving(null);
    }
  };

  if (live.length === 0) return null;

  return (
    <div className="border border-gray-200 p-4">
      <h2 className="font-display text-base text-primary mb-1">Busy mode</h2>
      <p className="text-xs text-gray-500 font-body mb-3">
        Take a branch offline for website and delivery-app orders — an on/off switch, normally thrown
        from the register during a rush. The website routes around it at once; each delivery app is
        closed within seconds.
      </p>
      {error && <p className="text-xs text-red-700 mb-2">{error}</p>}
      <ul className="divide-y divide-gray-100">
        {live.map((branch) => {
          const status = statuses[branch.id];
          const busy = !!status?.busy;
          return (
            <li key={branch.id} className="py-2 flex flex-wrap items-center gap-3 text-sm font-body">
              <span className="font-medium min-w-[10rem]">{branch.name}</span>
              {status === undefined ? (
                <Spinner />
              ) : busy ? (
                <span className="text-red-700 text-xs font-medium">
                  BUSY since {since(status.since)}
                  {status.by_name ? ` (${status.by_name})` : ''}
                </span>
              ) : (
                <span className="text-xs text-gray-500">Trading normally</span>
              )}
              <span className="flex flex-wrap gap-1">
                {status?.channels
                  ?.filter((c) => busy || c.state === 'opening' || c.state === 'failed')
                  .map((c) => (
                    <span
                      key={c.channel}
                      title={c.error ?? undefined}
                      className={`border px-1.5 py-0.5 text-[11px] ${CHIP_TONE[c.state]}`}
                    >
                      {c.label}: {CHIP_TEXT[c.state]}
                    </span>
                  ))}
              </span>
              <span className="ml-auto">
                <Button
                  size="sm"
                  variant={busy ? 'primary' : 'secondary'}
                  disabled={saving === branch.id || status === undefined}
                  onClick={() => void toggle(branch, !busy)}
                >
                  {busy ? 'Turn off busy mode' : 'Turn on busy mode'}
                </Button>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
