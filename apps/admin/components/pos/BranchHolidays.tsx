'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { branchesApi } from '@/lib/pos-api';
import { ApiError } from '@/lib/api';
import type { Branch, BranchHoliday, BranchHolidayWrite } from '@/lib/pos-types';
import { Button, Select, Spinner } from '@/components/ui';
import { useConfirm } from '@/components/ui/feedback';

/**
 * Days a branch is shut.
 *
 * **Exceptions only, and whole days only.** The shop trades seven days a week,
 * so there is no weekday rule to configure and a Saturday is an ordinary
 * working day; the only thing that closes a branch is a date written down here.
 * And a closure has no hours — a branch opening late is a change to its trading
 * hours, which are a field on the branch itself. Two ways to say "shut at 3pm"
 * would be two answers free to disagree.
 *
 * This is wired into the delivery estimate rather than being a note to staff.
 * Adding a date moves what every customer in this branch's zones is quoted,
 * from the next request onward: a batch run does not leave on a closed day, a
 * third-party handover waits for the next open one, and a courier that promises
 * minutes starts its clock at the next opening instead of now. Orders already
 * quoted keep what they were told — that is a record, not a derivation.
 */

function today(): string {
  // The shop's calendar, not the browser's. An admin in another timezone must
  // not be offered yesterday as the default closure.
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Dubai' }).format(new Date());
}

function longDate(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00+04:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return new Intl.DateTimeFormat('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'Asia/Dubai',
  }).format(parsed);
}

export function BranchHolidays() {
  const confirm = useConfirm();
  // Fetched here rather than handed down from the table above. `ResourcePage`
  // owns its own list and reloads it after every edit, so a copy passed in as
  // a prop would be the stale one the moment somebody renames a branch.
  const [branches, setBranches] = useState<Branch[]>([]);
  const servable = useMemo(
    // A deleted branch has no customers waiting on a promise, and an inactive
    // one has none either. Listing them would lengthen the picker without
    // making any closure mean anything.
    () => branches.filter(b => !b.deleted_at && b.is_active),
    [branches],
  );
  const [branchId, setBranchId] = useState('');
  const [holidays, setHolidays] = useState<BranchHoliday[] | null>(null);
  const [includePast, setIncludePast] = useState(false);
  const [draft, setDraft] = useState<BranchHolidayWrite | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    branchesApi
      .list()
      .then(setBranches)
      .catch(err => setError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  useEffect(() => {
    if (!branchId && servable.length) setBranchId(servable[0].id);
  }, [servable, branchId]);

  const load = useCallback(async () => {
    if (!branchId) return;
    setHolidays(null);
    try {
      setHolidays(await branchesApi.holidays(branchId, includePast));
      setError('');
    } catch (err) {
      setHolidays([]);
      setError(err instanceof ApiError ? err.message : 'Could not load the closures.');
    }
  }, [branchId, includePast]);

  useEffect(() => { load(); }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await action();
      await load();
      setDraft(null);
    } catch (err) {
      // A duplicate date comes back as a 409 naming the branch and the day.
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!servable.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        {error || 'No active branches to close.'}
      </div>
    );
  }

  const branch = servable.find(b => b.id === branchId);

  return (
    <div className="bg-white border border-gray-200">
      <header className="px-4 py-3 border-b border-gray-100">
        <h2 className="text-sm font-body text-gray-800">Holidays</h2>
        <p className="text-[11px] font-body text-gray-400 mt-1">
          Whole days this branch does not trade. Weekends are ordinary working
          days — only the dates listed here close the shop. The delivery
          estimate reads them, so a closure pushes what customers in this
          branch&apos;s zones are quoted; orders already placed keep the time
          they were given.
        </p>
      </header>

      <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center gap-3">
        <Select
          value={branchId}
          onChange={e => setBranchId(e.target.value)}
          options={servable.map(b => ({ value: b.id, label: `${b.name} (${b.reference})` }))}
        />
        <label className="inline-flex items-center gap-1.5 text-[11px] font-body text-gray-500">
          <input
            type="checkbox"
            checked={includePast}
            onChange={e => setIncludePast(e.target.checked)}
          />
          Show past closures
        </label>
        {branch && (
          <span className="text-[11px] font-body text-gray-400">
            Trades on its weekly schedule on every other day
          </span>
        )}
      </div>

      {error && (
        <p className="mx-4 mt-3 px-3 py-2 text-xs font-body text-red-700 bg-red-50 border border-red-200">
          {error}
        </p>
      )}

      {holidays === null ? (
        <div className="py-8 flex justify-center"><Spinner /></div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Date</th>
              <th className="px-3 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Occasion</th>
              <th className="px-3 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Note</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {holidays.map(holiday => (
              <HolidayRow
                key={`${holiday.id}:${holiday.holiday_date}:${holiday.name}`}
                holiday={holiday}
                busy={busy}
                onSave={data => run(() => branchesApi.updateHoliday(holiday.id, data))}
                onDelete={async () => {
                  if (await confirm({
                    title: 'Reopen this day',
                    message:
                      `Remove "${holiday.name}" on ${longDate(holiday.holiday_date)}? ` +
                      'Delivery estimates will treat it as a normal trading day again.',
                    confirmLabel: 'Remove',
                    danger: true,
                  })) void run(() => branchesApi.removeHoliday(holiday.id));
                }}
              />
            ))}
            {!holidays.length && !draft && (
              <tr>
                <td colSpan={4} className="px-4 py-4 text-xs font-body text-gray-400">
                  No closures{includePast ? '' : ' ahead'}. This branch trades every day.
                </td>
              </tr>
            )}
            {draft && (
              <HolidayRow
                editing
                holiday={{
                  id: 'new',
                  branch_id: branchId,
                  note: null,
                  created_at: '',
                  updated_at: '',
                  ...draft,
                }}
                busy={busy}
                onSave={data => run(() => branchesApi.addHoliday(branchId, data as BranchHolidayWrite))}
                onDelete={() => setDraft(null)}
              />
            )}
          </tbody>
        </table>
      )}

      {!draft && (
        <div className="px-4 py-3 border-t border-gray-100">
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => setDraft({ holiday_date: today(), name: '' })}
          >
            <span className="material-icons text-[14px]">event_busy</span>
            Close a day
          </Button>
        </div>
      )}
    </div>
  );
}

function HolidayRow({
  holiday,
  busy,
  editing: startEditing = false,
  onSave,
  onDelete,
}: {
  holiday: BranchHoliday;
  busy: boolean;
  editing?: boolean;
  onSave: (data: Partial<BranchHolidayWrite>) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(startEditing);
  const [form, setForm] = useState<BranchHolidayWrite>({
    holiday_date: holiday.holiday_date,
    name: holiday.name,
    note: holiday.note,
  });

  if (!editing) {
    const past = holiday.holiday_date < today();
    return (
      <tr className={past ? 'opacity-50' : ''}>
        <td className="px-4 py-2.5 text-xs font-body text-gray-800 whitespace-nowrap">
          {longDate(holiday.holiday_date)}
          {past && <span className="text-gray-400"> · past</span>}
        </td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-800">{holiday.name}</td>
        <td className="px-3 py-2.5 text-xs font-body text-gray-400">{holiday.note || '—'}</td>
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

  const cell =
    'w-full px-2 py-1 text-xs font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary';
  return (
    <tr className="bg-gray-50">
      <td className="px-4 py-2.5">
        <input
          type="date"
          value={form.holiday_date}
          onChange={e => setForm({ ...form, holiday_date: e.target.value })}
          className={cell}
        />
      </td>
      <td className="px-3 py-2.5">
        <input
          value={form.name}
          placeholder="Eid al-Fitr"
          onChange={e => setForm({ ...form, name: e.target.value })}
          className={cell}
        />
      </td>
      <td className="px-3 py-2.5">
        <input
          value={form.note ?? ''}
          placeholder="Optional"
          onChange={e => setForm({ ...form, note: e.target.value })}
          className={cell}
        />
      </td>
      <td className="px-3 py-2.5 text-right whitespace-nowrap">
        <button
          onClick={() => onSave({ ...form, note: form.note || null })}
          // A closure with no name is a date nobody can account for later,
          // which is the question this table exists to answer.
          disabled={busy || !form.name.trim() || !form.holiday_date}
          className="text-[11px] font-body text-primary hover:underline mr-3 disabled:text-gray-300 disabled:no-underline"
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
