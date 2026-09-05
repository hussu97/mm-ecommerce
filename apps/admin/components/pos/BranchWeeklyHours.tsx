'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { branchesApi } from '@/lib/pos-api';
import { ApiError } from '@/lib/api';
import type { Branch, WeeklyShift } from '@/lib/pos-types';
import { Button, Select, Spinner } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';

/**
 * A branch's weekly opening hours — the source of truth for when it trades.
 *
 * **One shift a day.** Each weekday is either open (one opening and one closing
 * time) or closed; there are deliberately no split shifts, because Melting
 * Moments trades one continuous shift a day and a second one would be a second
 * answer to "when does it open". A closed weekday reads exactly like a holiday.
 *
 * This is what the delivery estimate, the POS and the storefront all follow:
 * each resolves its window from *this* schedule directly (there is no separate
 * single-window field any more), and the marketplaces are sent this schedule.
 * Editing here moves all of them.
 */

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

interface DayRow {
  open: boolean;
  opens: string;
  closes: string;
}

function emptyWeek(opens: string, closes: string): DayRow[] {
  return DAYS.map(() => ({ open: false, opens, closes }));
}

function fromShifts(shifts: WeeklyShift[], opens: string, closes: string): DayRow[] {
  const week = emptyWeek(opens, closes);
  for (const s of shifts) {
    if (s.weekday >= 0 && s.weekday <= 6) {
      week[s.weekday] = { open: true, opens: s.opens, closes: s.closes };
    }
  }
  return week;
}

function toShifts(week: DayRow[]): WeeklyShift[] {
  return week
    .map((d, weekday) => ({ ...d, weekday }))
    .filter((d) => d.open)
    .map(({ weekday, opens, closes }) => ({ weekday, opens, closes }));
}

export function BranchWeeklyHours() {
  const toast = useToast();
  // Fetched here, not passed down: `ResourcePage` owns its own list and reloads
  // it after every edit, so a prop copy would be stale the moment a branch is
  // renamed — the same reasoning as BranchHolidays beside it.
  const [branches, setBranches] = useState<Branch[]>([]);
  const servable = useMemo(
    () => branches.filter((b) => !b.deleted_at && b.is_active),
    [branches],
  );
  const [branchId, setBranchId] = useState('');
  const [week, setWeek] = useState<DayRow[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState('');
  // The single open/close used by "Apply to whole week". Seeded from the
  // branch's current window so the common case — every day the same — is one
  // click.
  const [bulk, setBulk] = useState({ opens: '09:00', closes: '23:00' });

  useEffect(() => {
    branchesApi
      .list()
      .then(setBranches)
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  useEffect(() => {
    if (!branchId && servable.length) setBranchId(servable[0].id);
  }, [servable, branchId]);

  const load = useCallback(async () => {
    if (!branchId) return;
    setWeek(null);
    // Seed the "apply to whole week" bulk row with a sensible default; the
    // saved schedule is the source of truth once loaded.
    const fallbackOpen = '09:00';
    const fallbackClose = '23:00';
    setBulk({ opens: fallbackOpen, closes: fallbackClose });
    try {
      const res = await branchesApi.weeklyHours(branchId);
      setWeek(fromShifts(res.shifts ?? [], fallbackOpen, fallbackClose));
      setError('');
    } catch (err) {
      setWeek(emptyWeek(fallbackOpen, fallbackClose));
      setError(err instanceof ApiError ? err.message : 'Could not load the hours.');
    }
  }, [branchId]);

  useEffect(() => {
    load();
  }, [load]);

  const setDay = (weekday: number, patch: Partial<DayRow>) =>
    setWeek((w) => (w ? w.map((d, i) => (i === weekday ? { ...d, ...patch } : d)) : w));

  const applyToWholeWeek = () =>
    setWeek((w) =>
      w ? w.map(() => ({ open: true, opens: bulk.opens, closes: bulk.closes })) : w,
    );

  const save = async () => {
    if (!week) return;
    setSaving(true);
    setError('');
    try {
      const res = await branchesApi.setWeeklyHours(branchId, { shifts: toShifts(week) });
      setWeek(fromShifts(res.shifts ?? [], '09:00', '23:00'));
      toast.success('Weekly hours saved');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed');
      toast.error(err instanceof ApiError ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const syncNow = async () => {
    setSyncing(true);
    setError('');
    try {
      const res = (await branchesApi.syncHours(branchId)) as {
        window?: string | null;
        status?: string;
      };
      toast.success(
        res.window
          ? `Hours synced — today's window is ${res.window}`
          : 'Hours synced',
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Sync failed');
      toast.error(err instanceof ApiError ? err.message : 'Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  if (!servable.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        {error || 'No active branches to set hours for.'}
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200">
      <header className="px-4 py-3 border-b border-gray-100 flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-body text-gray-800">Weekly hours</h2>
          <p className="text-[11px] font-body text-gray-400 mt-1 max-w-2xl">
            When this branch trades — one shift a day, a day left closed is shut. The
            source of truth: the delivery estimate, the POS and the marketplaces all
            follow it. Changes take effect from the next request; the daily hours sync
            pushes them to the integrators.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={syncNow}
            loading={syncing}
            disabled={!week}
            title="Push today's hours to the storefront and the marketplaces now, instead of waiting for the hourly sync."
          >
            Sync now
          </Button>
          <Button size="sm" onClick={save} loading={saving} disabled={!week}>
            Save hours
          </Button>
        </div>
      </header>

      <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center gap-3">
        <Select
          value={branchId}
          onChange={(e) => setBranchId(e.target.value)}
          options={servable.map((b) => ({ value: b.id, label: `${b.name} (${b.reference})` }))}
        />
      </div>

      {error && (
        <p className="mx-4 mt-3 px-3 py-2 text-xs font-body text-red-700 bg-red-50 border border-red-200">
          {error}
        </p>
      )}

      {week === null ? (
        <div className="py-8 flex justify-center">
          <Spinner />
        </div>
      ) : (
        <>
          {/* Apply-to-whole-week override: the common case is every day the same,
              so set one window and stamp it across all seven days in a click. */}
          <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center gap-2 text-xs font-body text-gray-600 bg-gray-50">
            <span>Set every day to</span>
            <input
              type="time"
              value={bulk.opens}
              onChange={(e) => setBulk((b) => ({ ...b, opens: e.target.value }))}
              className="px-2 py-1 border border-gray-300 rounded-sm outline-none focus:border-primary"
            />
            <span className="text-gray-400">–</span>
            <input
              type="time"
              value={bulk.closes}
              onChange={(e) => setBulk((b) => ({ ...b, closes: e.target.value }))}
              className="px-2 py-1 border border-gray-300 rounded-sm outline-none focus:border-primary"
            />
            <Button size="sm" variant="ghost" onClick={applyToWholeWeek}>
              Apply to whole week
            </Button>
          </div>

          <table className="w-full text-sm">
            <tbody className="divide-y divide-gray-100">
              {DAYS.map((label, weekday) => {
                const d = week[weekday];
                return (
                  <tr key={weekday} className={d.open ? '' : 'bg-gray-50'}>
                    <td className="px-4 py-2.5 w-40">
                      <label className="inline-flex items-center gap-2 text-xs font-body text-gray-800">
                        <input
                          type="checkbox"
                          checked={d.open}
                          onChange={(e) => setDay(weekday, { open: e.target.checked })}
                        />
                        {label}
                      </label>
                    </td>
                    <td className="px-3 py-2.5">
                      {d.open ? (
                        <span className="inline-flex items-center gap-2 text-xs font-body">
                          <input
                            type="time"
                            value={d.opens}
                            onChange={(e) => setDay(weekday, { opens: e.target.value })}
                            className="px-2 py-1 border border-gray-300 rounded-sm outline-none focus:border-primary"
                          />
                          <span className="text-gray-400">–</span>
                          <input
                            type="time"
                            value={d.closes}
                            onChange={(e) => setDay(weekday, { closes: e.target.value })}
                            className="px-2 py-1 border border-gray-300 rounded-sm outline-none focus:border-primary"
                          />
                        </span>
                      ) : (
                        <span className="text-xs font-body text-gray-400">Closed</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
