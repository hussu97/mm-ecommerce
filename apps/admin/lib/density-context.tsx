'use client';

/**
 * Comfortable vs compact, remembered per browser.
 *
 * The console was built at a Mac Retina desk where the compact type scale reads
 * fine; on Windows the OS applies 125–150% display scaling, so the same pixels
 * come out larger and a wide table runs out of room sooner. Compact density buys
 * that room back without touching the type scale — it only tightens the row and
 * card padding, through the `--row-*` / `--card-*` variables in `globals.css`.
 *
 * The preset lives on `<html data-density>` so one attribute drives every table
 * at once. It is written in two places for one reason: a blocking inline script
 * in `app/layout.tsx` sets it before first paint (no flash of the wrong
 * density), and this provider keeps React's view of it in sync — but only ever
 * from an effect, never during render, so the server and client first paint
 * agree and there is no hydration mismatch.
 */

import { createContext, useCallback, useContext, useEffect, useState } from 'react';

export type Density = 'comfortable' | 'compact';

export const DENSITY_STORAGE_KEY = 'mm-admin-density';

/** Shared with the pre-paint script in `app/layout.tsx` — keep them in step. */
export function densityFromStorage(): Density {
  try {
    return localStorage.getItem(DENSITY_STORAGE_KEY) === 'compact' ? 'compact' : 'comfortable';
  } catch {
    // Private mode, or storage disabled — comfortable is the safe default.
    return 'comfortable';
  }
}

interface DensityContextValue {
  density: Density;
  setDensity: (density: Density) => void;
  toggleDensity: () => void;
}

const DensityContext = createContext<DensityContextValue | null>(null);

export function DensityProvider({ children }: { children: React.ReactNode }) {
  // Comfortable on the server and on the very first client render, matching the
  // markup the server sent; the effect below reconciles with what the pre-paint
  // script already put on `<html>`, so nothing flashes and nothing mismatches.
  const [density, setDensityState] = useState<Density>('comfortable');

  useEffect(() => {
    setDensityState(densityFromStorage());
  }, []);

  const setDensity = useCallback((next: Density) => {
    setDensityState(next);
    document.documentElement.dataset.density = next;
    try {
      localStorage.setItem(DENSITY_STORAGE_KEY, next);
    } catch {
      // A remembered preference is a nicety, not a requirement.
    }
  }, []);

  const toggleDensity = useCallback(() => {
    setDensity(density === 'compact' ? 'comfortable' : 'compact');
  }, [density, setDensity]);

  return (
    <DensityContext.Provider value={{ density, setDensity, toggleDensity }}>
      {children}
    </DensityContext.Provider>
  );
}

export function useDensity(): DensityContextValue {
  const ctx = useContext(DensityContext);
  if (!ctx) throw new Error('useDensity must be used within a DensityProvider');
  return ctx;
}
