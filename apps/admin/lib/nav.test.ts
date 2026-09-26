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

import {
  NAV,
  SUBROUTE_REQUIRES,
  canAccessConsole,
  canAccessNav,
  holdsPermission,
  requiredPermissionFor,
  type NavEntry,
} from './nav';

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

  it('names only real slugs in the sub-route gates too', () => {
    const slugs = serverSlugs();
    const unknown = Object.entries(SUBROUTE_REQUIRES)
      .flatMap(([path, req]) => req.filter((s) => !slugs.has(s)).map((s) => `${path} → ${s}`));
    expect(unknown, 'these SUBROUTE_REQUIRES are not real permission slugs').toEqual([]);
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

describe('page gate', () => {
  it('takes the owning entry\'s slug for an ordinary route and its detail pages', () => {
    expect(requiredPermissionFor('/purchase-orders')).toBe('inventory.purchase_orders.manage');
    expect(requiredPermissionFor('/purchase-orders/some-po-id')).toBe('inventory.purchase_orders.manage');
    // The misc category / period tabs share the order list's gate, as their API does.
    expect(requiredPermissionFor('/purchase-orders/misc-categories')).toBe('inventory.purchase_orders.manage');
    expect(requiredPermissionFor('/purchase-orders/misc-periods')).toBe('inventory.purchase_orders.manage');
    expect(requiredPermissionFor('/security')).toBeNull();
    expect(requiredPermissionFor('/no-such-screen')).toBeNull();
  });

  it('gates Suppliers (a Purchase Orders tab) on inventory.read, like its API', () => {
    // Moved from Inventory: without its own gate it would inherit the section's
    // PO slug and lock out the inventory staff who used it there.
    const needed = requiredPermissionFor('/purchase-orders/suppliers');
    expect(needed).toEqual(['inventory.read']);
    expect(holdsPermission(needed, { permissions: ['inventory.read'] })).toBe(true);
    expect(holdsPermission(needed, { permissions: ['inventory.purchase_orders.manage'] })).toBe(false);
    expect(holdsPermission(needed, { permissions: ['orders.read'] })).toBe(false);
    expect(holdsPermission(needed, { is_superadmin: true, permissions: [] })).toBe(true);
  });

  it('checks a single slug, a public screen, and the signed-out', () => {
    expect(holdsPermission('orders.read', { permissions: ['orders.read'] })).toBe(true);
    expect(holdsPermission('orders.read', { permissions: [] })).toBe(false);
    expect(holdsPermission(null, { permissions: [] })).toBe(true);
    expect(holdsPermission(null, null)).toBe(false);
  });
});

describe('console access gate', () => {
  it('lets in a super-admin, or any staff member with a role permission', () => {
    // A limited-role cashier (one permission) may enter and is then narrowed by
    // canAccessNav — this is what "add cashier staff as console users" turns on.
    expect(canAccessConsole({ permissions: ['inventory.transfers.manage'] })).toBe(true);
    // Super-admin needs no enumerated permissions.
    expect(canAccessConsole({ is_superadmin: true, permissions: [] })).toBe(true);
  });

  it('keeps out a customer (no role, no permissions) and the signed-out', () => {
    // A shopper who authenticates on the shared /auth/login has no permissions,
    // so the console door stays shut even though the token is valid.
    expect(canAccessConsole({ permissions: [] })).toBe(false);
    expect(canAccessConsole({ is_superadmin: false, permissions: [] })).toBe(false);
    expect(canAccessConsole(null)).toBe(false);
    expect(canAccessConsole(undefined)).toBe(false);
  });
});
