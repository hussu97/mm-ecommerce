import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Branch } from '@/lib/pos-types';

import { defaultBranchId } from './CostBreakdownModal';

const branch = (id: string, name: string, type: Branch['type'] = 'restaurant') =>
  ({ id, name, type }) as Branch;

const branches = [
  branch('krm', 'Al Karama'),
  branch('shj', 'Sharjah Kitchen', 'kitchen'),
  branch('b001', 'Barsha Heights'),
];

describe('cost breakdown default branch', () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    });
  });

  it('opens on the Sharjah kitchen when nothing is remembered', () => {
    expect(defaultBranchId(branches)).toBe('shj');
  });

  it('reopens on the branch last picked on this device', () => {
    localStorage.setItem('mm-admin-cost-breakdown-branch', 'b001');
    expect(defaultBranchId(branches)).toBe('b001');
  });

  it('remembers "All branches" too', () => {
    localStorage.setItem('mm-admin-cost-breakdown-branch', '');
    expect(defaultBranchId(branches)).toBe('');
  });

  it('falls back to Sharjah when the remembered branch is no longer active', () => {
    localStorage.setItem('mm-admin-cost-breakdown-branch', 'closed-branch');
    expect(defaultBranchId(branches)).toBe('shj');
  });
});
