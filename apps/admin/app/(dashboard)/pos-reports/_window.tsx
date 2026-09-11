'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { Input, Select } from '@/components/ui';

import { defaultWindow, windowFromParams } from './report-window';

/**
 * The branch + date-range control shared by every POS report tab.
 *
 * It used to be `useState` at the top of one page that owned all six tabs. Each
 * tab is its own route now, so the window can no longer live in a parent
 * component's state — it lives in the URL instead (`?from=&to=&branch=`) and
 * every tab reads it back with `windowFromParams`. This component is the one
 * place that writes it: changing a field rewrites the query string (without a
 * navigation, so the current tab and its scroll position stay put), and the
 * tab re-fetches against the new window.
 */
export function ReportWindow() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { branch, from, to } = windowFromParams(searchParams);
  const [branches, setBranches] = useState<Branch[]>([]);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  // Seed the default window into the URL on first visit, so a bookmark or a
  // reload carries the same last-7-days the old in-page default gave, and the
  // date pickers below always show a concrete value.
  useEffect(() => {
    if (searchParams.has('from') && searchParams.has('to')) return;
    const def = defaultWindow();
    const next = new URLSearchParams(searchParams.toString());
    if (!next.has('from')) next.set('from', def.from);
    if (!next.has('to')) next.set('to', def.to);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, pathname]);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams.toString());
    if (value) next.set(key, value);
    else next.set(key, '');
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  return (
    <div className="mb-3 flex flex-wrap items-end gap-3">
      <Select
        label="Branch"
        value={branch}
        onChange={(e) => setParam('branch', e.target.value)}
        options={branches.map((b) => ({ value: b.id, label: b.name }))}
        placeholder="All branches"
        className="w-52"
      />
      <Input
        label="From"
        type="date"
        value={from}
        onChange={(e) => setParam('from', e.target.value)}
        className="w-40"
      />
      <Input
        label="To"
        type="date"
        value={to}
        onChange={(e) => setParam('to', e.target.value)}
        className="w-40"
      />
    </div>
  );
}
