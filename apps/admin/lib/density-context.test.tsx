import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DENSITY_STORAGE_KEY,
  DensityProvider,
  densityFromStorage,
  useDensity,
} from './density-context';

// happy-dom in this project ships a `localStorage` whose methods are undefined,
// so stand up a Map-backed Storage the code under test can actually read/write.
function installStorage() {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k) => (store.has(k) ? store.get(k)! : null),
    key: (i) => Array.from(store.keys())[i] ?? null,
    removeItem: (k) => void store.delete(k),
    setItem: (k, v) => void store.set(k, String(v)),
  };
  vi.stubGlobal('localStorage', storage);
}

function Probe() {
  const { density, setDensity, toggleDensity } = useDensity();
  return (
    <div>
      <span data-testid="density">{density}</span>
      <button onClick={() => setDensity('compact')}>set-compact</button>
      <button onClick={toggleDensity}>toggle</button>
    </div>
  );
}

describe('density-context', () => {
  beforeEach(() => {
    installStorage();
    delete document.documentElement.dataset.density;
  });

  it('reads compact/comfortable from storage, defaulting to comfortable', () => {
    expect(densityFromStorage()).toBe('comfortable');
    localStorage.setItem(DENSITY_STORAGE_KEY, 'compact');
    expect(densityFromStorage()).toBe('compact');
    localStorage.setItem(DENSITY_STORAGE_KEY, 'nonsense');
    expect(densityFromStorage()).toBe('comfortable');
  });

  it('hydrates from storage into the provider', () => {
    localStorage.setItem(DENSITY_STORAGE_KEY, 'compact');
    render(
      <DensityProvider>
        <Probe />
      </DensityProvider>,
    );
    // The effect runs after mount and reconciles to the stored value.
    expect(screen.getByTestId('density').textContent).toBe('compact');
  });

  it('persists a change to storage and to <html data-density>', () => {
    render(
      <DensityProvider>
        <Probe />
      </DensityProvider>,
    );
    act(() => {
      screen.getByText('set-compact').click();
    });
    expect(screen.getByTestId('density').textContent).toBe('compact');
    expect(localStorage.getItem(DENSITY_STORAGE_KEY)).toBe('compact');
    expect(document.documentElement.dataset.density).toBe('compact');
  });

  it('toggles back and forth', () => {
    render(
      <DensityProvider>
        <Probe />
      </DensityProvider>,
    );
    act(() => screen.getByText('toggle').click());
    expect(screen.getByTestId('density').textContent).toBe('compact');
    act(() => screen.getByText('toggle').click());
    expect(screen.getByTestId('density').textContent).toBe('comfortable');
    expect(localStorage.getItem(DENSITY_STORAGE_KEY)).toBe('comfortable');
  });
});
