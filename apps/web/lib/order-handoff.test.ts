import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  stashOrderHandoff,
  readOrderHandoff,
  clearOrderHandoff,
} from './order-handoff';

const sessionStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { store = {}; },
    key: (i: number) => Object.keys(store)[i] ?? null,
    get length() { return Object.keys(store).length; },
  };
})();

beforeEach(() => {
  vi.stubGlobal('sessionStorage', sessionStorageMock);
  sessionStorageMock.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('order hand-off (F-WEB-2)', () => {
  it('round-trips the email for the order it was stashed for', () => {
    stashOrderHandoff({ order_number: 'MM-1', email: 'jane@example.com' });
    expect(readOrderHandoff('MM-1')).toEqual({
      order_number: 'MM-1',
      email: 'jane@example.com',
    });
  });

  it('is not replayed against a different order number', () => {
    stashOrderHandoff({ order_number: 'MM-1', email: 'jane@example.com' });
    // A stale hand-off from an earlier order must not open a later one.
    expect(readOrderHandoff('MM-2')).toBeNull();
  });

  it('returns null when nothing was stashed', () => {
    expect(readOrderHandoff('MM-1')).toBeNull();
  });

  it('clears the stash', () => {
    stashOrderHandoff({ order_number: 'MM-1', email: 'jane@example.com' });
    clearOrderHandoff();
    expect(readOrderHandoff('MM-1')).toBeNull();
  });

  it('carries a token as well as an email', () => {
    stashOrderHandoff({ order_number: 'MM-1', token: 'tok_abc' });
    expect(readOrderHandoff('MM-1')?.token).toBe('tok_abc');
  });

  it('does not throw when storage is unavailable', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => { throw new Error('insecure'); },
      setItem: () => { throw new Error('insecure'); },
      removeItem: () => { throw new Error('insecure'); },
    });
    expect(() => stashOrderHandoff({ order_number: 'MM-1', email: 'x@y.com' })).not.toThrow();
    expect(readOrderHandoff('MM-1')).toBeNull();
    expect(() => clearOrderHandoff()).not.toThrow();
  });
});
