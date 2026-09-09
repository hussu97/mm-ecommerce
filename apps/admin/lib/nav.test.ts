/**
 * The sidebar shows only the screens a user's API access would serve (F-ADM-7).
 *
 * The load-bearing part is the `requires` slug on each entry: a wrong slug hides
 * a screen from someone who should see it, or shows one the API will 403. These
 * hold the map honest — every slug is a real one the backend defines, and every
 * screen entry declares its gate explicitly (a `null` is a deliberate "public").
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { NAV, canAccessNav, type NavEntry } from './nav';

const ADMIN = join(__dirname, '..');

function serverSlugs(): Set<string> {
  const py = readFileSync(
    join(ADMIN, '..', 'api', 'app', 'models', 'role.py'),
    'utf8',
  );
  // PERMISSION_GROUPS is a dict of group -> list of ("slug", "description").
  const start = py.indexOf('PERMISSION_GROUPS');
  const end = py.indexOf('ALL_PERMISSIONS');
  const block = py.slice(start, end === -1 ? undefined : end);
  return new Set([...block.matchAll(/\(\s*"([a-z_.]+)"\s*,/g)].map((m) => m[1]));
}

const entries = NAV.filter((e): e is NavEntry => 'href' in e);

describe('sidebar nav permissions', () => {
  it('gives every screen an explicit requirement (a slug or null)', () => {
    const missing = entries
      .filter((e) => e.requires === undefined)
      .map((e) => e.href);
    expect(missing, 'these nav entries have no `requires` — gate them').toEqual([]);
  });

  it('names only permission slugs the backend actually defines', () => {
    const slugs = serverSlugs();
    expect(slugs.size).toBeGreaterThan(20); // the parser found the catalogue
    const unknown = entries
      .filter((e) => e.requires !== null && !slugs.has(e.requires))
      .map((e) => `${e.href} → ${e.requires}`);
    expect(unknown, 'these `requires` are not real permission slugs').toEqual([]);
  });

  it('gates entries by the viewer, and a super-admin sees everything', () => {
    const orders = entries.find((e) => e.href === '/orders')!;
    const security = entries.find((e) => e.href === '/security')!; // requires: null

    expect(canAccessNav(orders, { permissions: ['orders.read'] })).toBe(true);
    expect(canAccessNav(orders, { permissions: ['catalogue.manage'] })).toBe(false);
    expect(canAccessNav(orders, { permissions: [] })).toBe(false);
    // A null requirement is public to any signed-in admin.
    expect(canAccessNav(security, { permissions: [] })).toBe(true);
    // Super-admin bypasses the slug check entirely.
    expect(canAccessNav(orders, { is_superadmin: true, permissions: [] })).toBe(true);
    // No user at all sees nothing.
    expect(canAccessNav(orders, null)).toBe(false);
  });
});
