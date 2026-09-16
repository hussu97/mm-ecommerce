/**
 * The two admin conventions from CLAUDE.md that had no teeth.
 *
 * Both were followed. That is exactly why they needed a test: a convention
 * nothing checks is followed until the day somebody has a reason not to, and
 * the pagination one had already drifted in coverage — the shared control was
 * used everywhere it appeared, and eleven tables did not appear.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  SETTLED_STATUSES,
  canCancel,
  canRefund,
} from '../app/(dashboard)/orders/[orderNumber]/order-status';
import type { Order } from './types';

const ADMIN = join(__dirname, '..');

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '.next') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const SOURCES = walk(ADMIN).filter((f) => !/\.test\.tsx?$/.test(f));

describe('workflow rule W8 — admin pagination', () => {
  it('offers exactly the standard page sizes, in order', () => {
    const ui = readFileSync(join(ADMIN, 'components/ui/index.tsx'), 'utf8');
    const match = ui.match(/const PER_PAGE_OPTIONS = \[([^\]]+)\]/);

    expect(match, 'PER_PAGE_OPTIONS has moved or been renamed').toBeTruthy();
    expect(match![1].split(',').map((n) => Number(n.trim()))).toEqual([
      50, 100, 200, 500, 1000, 2000,
    ]);
  });

  it('declares those sizes in one place', () => {
    // A second array of page sizes anywhere is the drift this rule exists to
    // stop: two controls offering different options on adjacent screens.
    const offenders = SOURCES.filter((f) => {
      if (f.endsWith(join('components', 'ui', 'index.tsx'))) return false;
      return /\[\s*(?:10|20|25|50)\s*,\s*\d+\s*,\s*\d+/.test(readFileSync(f, 'utf8'));
    }).map((f) => f.slice(ADMIN.length + 1));

    expect(offenders, 'these look like a second page-size list').toEqual([]);
  });
});

describe('convention 9 — one request path', () => {
  it('no screen calls fetch directly', () => {
    // `lib/api.ts` owns `request()`, and with it the 401 refresh-and-retry.
    // Two copies of a CSV download bypassed it and threw on an expired
    // session instead of refreshing; that is what this stops recurring.
    const SANCTIONED = [join('lib', 'api.ts')];
    const offenders: string[] = [];

    for (const file of SOURCES) {
      const rel = file.slice(ADMIN.length + 1);
      if (SANCTIONED.some((s) => rel === s)) continue;
      if (rel.startsWith('scripts' + sep)) continue;
      const body = readFileSync(file, 'utf8');
      // `refetch(` and `prefetch(` are not `fetch(`.
      if (/(^|[^A-Za-z0-9_.])fetch\s*\(/m.test(body)) offenders.push(rel);
    }

    expect(offenders, 'these bypass lib/api.ts request()').toEqual([]);
  });
});

describe('F-ADM-9 — settled statuses mirror the server', () => {
  it('lists exactly the statuses in _SETTLED_STATUSES (app/api/v1/orders.py)', () => {
    // The order detail screen gates its courier buttons on these; the server
    // enforces `_assert_still_going_somewhere` against the same set, so a status
    // the client does not treat as settled shows a button that can only 409.
    const py = readFileSync(
      join(ADMIN, '..', 'api', 'app', 'api', 'v1', 'orders.py'),
      'utf8',
    );
    const block = py.match(/_SETTLED_STATUSES\s*=\s*\{([^}]*)\}/);
    expect(block, '_SETTLED_STATUSES moved or was renamed in orders.py').toBeTruthy();

    const server = [...block![1].matchAll(/OrderStatusEnum\.(\w+)/g)]
      .map((m) => m[1].toLowerCase())
      .sort();
    expect(server.length).toBeGreaterThan(1); // the matcher actually found them

    expect(
      [...SETTLED_STATUSES].sort(),
      'SETTLED_STATUSES in order-status.ts has drifted from the server set',
    ).toEqual(server);
  });
});

describe('F-ADM-16 — the Cancel button never offers what the server refuses', () => {
  const LIFECYCLE = join(
    ADMIN,
    '..',
    'api',
    'app',
    'services',
    'orders',
    'order_lifecycle.py',
  );

  const order = (status: string, source: string) =>
    ({ status, source }) as unknown as Pick<Order, 'status' | 'source'>;

  it('only online/aggregator (never cashier) may cancel a packed order', () => {
    // The whole bug: a packed COUNTER order showed a Cancel button the server
    // 409s. Only ONLINE/AGGREGATOR have a packed hatch server-side.
    expect(canCancel(order('packed', 'online'))).toBe(true);
    expect(canCancel(order('packed', 'aggregator'))).toBe(true);
    expect(canCancel(order('packed', 'cashier'))).toBe(false);
  });

  it('offers the live states for every source and offers nothing once shipped', () => {
    for (const source of ['cashier', 'online', 'aggregator']) {
      for (const status of ['created', 'confirmed', 'arrived_at_pos']) {
        expect(canCancel(order(status, source)), `${status}/${source}`).toBe(true);
      }
      for (const status of ['out_for_delivery', 'delivered', 'cancelled', 'refunded']) {
        expect(canCancel(order(status, source)), `${status}/${source}`).toBe(false);
      }
    }
  });

  it('matches the Python: a packed hatch exists for online+aggregator only', () => {
    const py = readFileSync(LIFECYCLE, 'utf8');
    // Both source hatches are exactly {PACKED} …
    for (const name of ['ONLINE_CANCELLABLE_FROM', 'AGGREGATOR_CANCELLABLE_FROM']) {
      const block = py.match(new RegExp(`${name}[^=]*=\\s*frozenset\\(\\s*\\{([^}]*)\\}`));
      expect(block, `${name} moved or was renamed`).toBeTruthy();
      const states = [...block![1].matchAll(/OrderStatusEnum\.(\w+)/g)].map(m =>
        m[1].toLowerCase(),
      );
      expect(states, `${name} is no longer just {PACKED} — revisit canCancel`).toEqual([
        'packed',
      ]);
    }
    // … and there is no cashier hatch, which is why a packed counter order stays
    // uncancellable. If one is ever added, canCancel must learn about it.
    expect(py.includes('CASHIER_CANCELLABLE_FROM')).toBe(false);
  });
});

describe('the Refund button never offers what the server refuses', () => {
  const ORDERS_ROUTE = join(
    ADMIN,
    '..',
    'api',
    'app',
    'api',
    'v1',
    'orders.py',
  );

  const order = (
    status: string,
    source: string,
    payment_provider: string | null,
  ) =>
    ({ status, source, payment_provider }) as unknown as Pick<
      Order,
      'status' | 'source' | 'payment_provider'
    >;

  it('offers a refund only on a delivered website order paid via stripe/ziina', () => {
    expect(canRefund(order('delivered', 'online', 'stripe'))).toBe(true);
    expect(canRefund(order('delivered', 'online', 'ziina'))).toBe(true);
    // Wrong provider, source, or a cash order — none refundable here.
    expect(canRefund(order('delivered', 'online', 'cod'))).toBe(false);
    expect(canRefund(order('delivered', 'aggregator', 'stripe'))).toBe(false);
    expect(canRefund(order('delivered', 'cashier', 'stripe'))).toBe(false);
    // Only a delivered order — a live one is cancelled instead, a settled one
    // is done.
    for (const status of ['confirmed', 'packed', 'out_for_delivery', 'cancelled', 'refunded']) {
      expect(canRefund(order(status, 'online', 'stripe')), status).toBe(false);
    }
  });

  it("matches the Python route's own gate", () => {
    // The refund route enforces the same three facts. If it changes — a new
    // provider, a different source, a status other than delivered — canRefund
    // must move with it.
    const py = readFileSync(ORDERS_ROUTE, 'utf8');
    expect(py).toContain('async def refund_order_admin');
    expect(py).toContain('order.source != OrderSourceEnum.ONLINE.value');
    expect(py).toContain('order.payment_provider not in ("stripe", "ziina")');
    expect(py).toContain('order.status != OrderStatusEnum.DELIVERED');
  });
});

describe('F-ADM pagination — every DataTable list carries the control', () => {
  // Files that render a <DataTable> but deliberately do NOT paginate, each with
  // the reason it is bounded. Shrink-only: a NEW unpaginated DataTable fails the
  // test below, and an entry whose file has gained <Pagination> (or lost its
  // DataTable) must be removed. Paths are relative to apps/admin, sep-normalised.
  const ALLOWED_WITHOUT_PAGINATION = new Map<string, string>([
    ['app/(dashboard)/admin-users/page.tsx', 'the admins/superadmins — a handful of rows'],
    ['app/(dashboard)/security/page.tsx', "the current admin's own passkeys — a handful"],
    ['app/(dashboard)/aggregators/fees/page.tsx', 'a per-channel fee summary, one row per channel'],
    ['app/(dashboard)/aggregators/logins/page.tsx', 'worker sessions, one per channel'],
    ['app/(dashboard)/aggregators/mappings/page.tsx', 'branch/channel maps, bounded by branch count'],
    ['app/(dashboard)/inventory/integrity/page.tsx', 'a diagnostic result set meant to be read whole'],
    ['app/(dashboard)/inventory/templates/page.tsx', 'report templates per branch — a few config rows'],
    ['app/(dashboard)/inventory/transfers/page.tsx', 'transfer templates per branch — config rows'],
    ['components/inventory/RecipeEditor.tsx', "an editor's ingredient rows, not a list screen"],
  ].map(([p, why]) => [p.split('/').join(sep), why]));

  it('a DataTable page renders <Pagination> or is a documented bounded list', () => {
    const offenders: string[] = [];
    for (const file of SOURCES) {
      const body = readFileSync(file, 'utf8');
      if (!body.includes('<DataTable')) continue;
      // The control itself, and the shared list scaffold that always pairs the
      // two, are not screens.
      const rel = file.slice(ADMIN.length + 1);
      if (
        rel === join('components', 'ui', 'DataTable.tsx') ||
        rel === join('components', 'ui', 'ListPage.tsx')
      ) {
        continue;
      }
      if (body.includes('<Pagination')) continue;
      if (ALLOWED_WITHOUT_PAGINATION.has(rel)) continue;
      offenders.push(rel);
    }
    expect(
      offenders,
      'These screens render a DataTable with no <Pagination> and are not in the ' +
        'bounded-list allow-list. Add the shared <Pagination> control (see ' +
        'languages/page.tsx or ResourcePage `paginated`), or, if the list is ' +
        'genuinely bounded, add it to ALLOWED_WITHOUT_PAGINATION with a reason.',
    ).toEqual([]);
  });

  it('the allow-list only shrinks — no stale entries', () => {
    for (const [rel] of ALLOWED_WITHOUT_PAGINATION) {
      const full = join(ADMIN, rel);
      const body = readFileSync(full, 'utf8');
      expect(body.includes('<DataTable'), `${rel} no longer renders a DataTable`).toBe(true);
      expect(
        body.includes('<Pagination'),
        `${rel} now paginates — remove it from ALLOWED_WITHOUT_PAGINATION`,
      ).toBe(false);
    }
  });
});

describe('F-ADM — superadmin is decided by the server, not a hardcoded email', () => {
  it('no source hardcodes a superadmin console email', () => {
    // A superadmin gate keyed off a literal address (security/page.tsx used
    // `admin@meltingmomentscakes.com`) goes stale the moment a second superadmin
    // exists; the server sends `is_superadmin` for exactly this.
    const offenders = SOURCES.filter((f) =>
      /['"]admin@meltingmomentscakes\.com['"]/.test(readFileSync(f, 'utf8')),
    ).map((f) => f.slice(ADMIN.length + 1));
    expect(offenders, 'gate on user.is_superadmin instead').toEqual([]);
  });
});
