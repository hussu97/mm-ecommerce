'use client';

import { useEffect, useState } from 'react';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';

/**
 * Loads the branch list once and derives the two shapes both device screens
 * need: `<select>` options for the create/edit form, and an id→name lookup for
 * the table. Each route fetches its own copy — the list is tiny and the two
 * screens are never mounted at the same time.
 */
export function useBranchOptions() {
  const [branches, setBranches] = useState<Branch[]>([]);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  const branchOptions = branches.map((b) => ({ value: b.id, label: b.name }));
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? '—';

  return { branchOptions, branchName };
}
