import { describe, expect, it } from 'vitest';
import { loginPathFor, safeNextPath } from './auth-redirect';

describe('auth redirect helpers', () => {
  it('keeps relative admin destinations', () => {
    expect(safeNextPath('?next=%2Forders%2FMM-20260606-001')).toBe('/orders/MM-20260606-001');
    expect(safeNextPath('?next=%2Forders%3Fsearch%3Dfatema')).toBe('/orders?search=fatema');
  });

  it('falls back for missing or external destinations', () => {
    expect(safeNextPath('')).toBe('/');
    expect(safeNextPath('?next=https%3A%2F%2Fevil.example')).toBe('/');
    expect(safeNextPath('?next=%2F%2Fevil.example')).toBe('/');
  });

  it('builds a login URL with the current admin destination', () => {
    expect(loginPathFor('/orders/MM-20260606-001', '?tab=items')).toBe(
      '/login?next=%2Forders%2FMM-20260606-001%3Ftab%3Ditems',
    );
  });
});
