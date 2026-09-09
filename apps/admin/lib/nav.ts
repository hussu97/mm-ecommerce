/**
 * The console's navigation, and the rule for which entry is lit.
 *
 * Its own module rather than a pair of exports from `layout.tsx`: a route file
 * is meant to export its default and Next's own configuration keys, and a
 * helper hanging off one is a helper that a framework upgrade is entitled to
 * complain about. It is also the only way to test the rule below without
 * mounting a layout.
 */

// Grouped by what the operator is doing, not by which system serves it: the old
// "Online store" section had grown into a fifteen-item drawer that mixed order
// ops, reporting, CMS, i18n and the whole aggregator relationship. Each group
// below is one job, and no group is larger than eight.
// `requires` is the permission slug the screen's own API gates on, so the entry
// is shown only to a user the API would actually serve (F-ADM-7) — a screen that
// would 403 is not offered as a dead link. `null` means "any signed-in admin"
// (e.g. Security manages the viewer's OWN passkeys). Each slug is verified
// against the route that backs the screen; `nav.test.ts` fails if one drifts
// from the Python `ALL_PERMISSIONS`.
export type NavEntry = {
  href: string;
  label: string;
  icon: string;
  match?: string;
  requires: string | null;
};
export type NavSection = { section: string };

export const NAV: Array<NavEntry | NavSection> = [
  { href: '/',              label: 'Dashboard',       icon: 'dashboard',    requires: 'dashboard.access' },

  { section: 'Catalog' },
  { href: '/products',      label: 'Products',        icon: 'inventory_2',  requires: 'catalogue.manage' },
  { href: '/categories',    label: 'Categories',      icon: 'category',     requires: 'catalogue.manage' },
  { href: '/modifiers',     label: 'Modifiers',       icon: 'tune',         requires: 'catalogue.manage' },
  { href: '/menu-groups',   label: 'Menu Groups',     icon: 'account_tree', requires: 'catalogue.manage' },
  { href: '/catalog-sync',  label: 'Catalog Sync',    icon: 'sync_alt',     requires: 'catalogue.manage' },

  // The transaction heart — every channel's orders, and the levers around a
  // sale. Counter orders live on the one Orders screen (the channels have
  // always shared a table); the register config lives under Operations.
  { section: 'Sales' },
  { href: '/orders',        label: 'Orders',          icon: 'receipt_long', requires: 'orders.read' },
  { href: '/custom-orders', label: 'Custom Orders',   icon: 'cake',         requires: 'orders.custom.manage' },
  { href: '/customers',     label: 'Customers',        icon: 'people',      requires: 'customers.read' },
  { href: '/promo-codes',   label: 'Promotions',      icon: 'local_offer',  requires: 'marketing.manage' },
  // "Payment Gateways", not "Payments" — the storefront processors (Stripe,
  // Ziina), distinct from POS tender types under POS Config. Kept in a
  // top-visible group: it is the lever you reach for during a processor
  // incident, not the moment to go hunting three sections down.
  { href: '/payment-gateways', label: 'Payment Gateways', icon: 'credit_card', requires: 'admin.payments.manage' },

  // The physical side: shops, tills, stock, and the config behind them.
  { section: 'Operations' },
  { href: '/branches',      label: 'Branches',        icon: 'storefront',   requires: 'admin.branches.manage' },
  { href: '/devices',       label: 'Terminals',       icon: 'tablet_mac',   requires: 'admin.devices.manage' },
  { href: '/staff',         label: 'Staff & Roles',   icon: 'badge',        requires: 'admin.users.manage' },
  { href: '/pos-config',    label: 'POS Config',      icon: 'settings_applications', requires: 'admin.settings.manage' },
  { href: '/inventory',     label: 'Inventory',       icon: 'warehouse',    requires: 'inventory.read' },
  { href: '/purchase-orders', label: 'Purchase Orders', icon: 'shopping_cart_checkout', requires: 'inventory.purchase_orders.manage' },
  // Delivery zones and which kitchen bakes each one — fulfilment config that
  // sits with the branches it routes to.
  { href: '/delivery-zones', label: 'Delivery',       icon: 'local_shipping', requires: 'delivery.manage' },

  // All reporting in one place. Live Baskets is now a tab of Analytics (the
  // same funnel one step earlier), not its own drawer item; "Counter Reports"
  // is the trading-day counter reporting.
  { section: 'Reports' },
  { href: '/analytics',     label: 'Analytics',       icon: 'bar_chart',    requires: 'reports.sales' },
  { href: '/pos-reports',   label: 'Counter Reports', icon: 'insights',     requires: 'reports.sales' },
  // One front door for marketplace reconciliation. GrubOps, invoices, VAT,
  // sync runs and mappings stay lateral tabs so the sidebar does not repeat
  // the same operational area several times.
  { href: '/aggregators/reconciliation', label: 'Reconciliation', icon: 'fact_check', match: '/aggregators', requires: 'reports.sales' },

  // What a URL says and where it leads. Redirects sits by Content because it is
  // the same job — and it holds the URLs nothing else knows about, like one
  // from the old Wix site or a printed card.
  { section: 'Content' },
  { href: '/content',       label: 'Content',         icon: 'edit_note',    requires: 'content.manage' },
  { href: '/redirects',     label: 'Redirects',       icon: 'alt_route',    requires: 'content.manage' },

  { section: 'Settings & System' },
  { href: '/languages',     label: 'Languages',       icon: 'translate',    requires: 'content.manage' },
  { href: '/translations',  label: 'Translations',    icon: 'text_fields',  requires: 'content.manage' },
  { href: '/admin-users',   label: 'Admin Users',     icon: 'admin_panel_settings', requires: 'admin.users.manage' },
  { href: '/import',        label: 'Import / Export', icon: 'sync_alt',     requires: 'admin.data.manage' },
  // Self-service: every admin manages their own passkeys here, so it needs no slug.
  { href: '/security',      label: 'Security',        icon: 'vpn_key',      requires: null },
  // Email, Webhook and Audit logs were three drawer items doing one job — "show
  // me what happened". One entry now, three tabs behind it (see `LogsTabs`); the
  // old paths redirect in.
  { href: '/logs',          label: 'Logs',            icon: 'article',      requires: 'admin.logs.read' },
];

/** Whether *user* may see/enter a nav entry. A super-admin sees everything; a
 *  `null` requirement is public to any signed-in admin; otherwise the user must
 *  hold the slug. */
export function canAccessNav(
  entry: NavEntry,
  user: { is_superadmin?: boolean; permissions?: string[] } | null | undefined,
): boolean {
  if (!user) return false;
  if (user.is_superadmin) return true;
  if (entry.requires === null) return true;
  return (user.permissions ?? []).includes(entry.requires);
}

/** The permission the screen at *pathname* needs, or null when the active entry
 *  is public or no entry owns the path. Used to gate the page body, so a
 *  deep-link to a screen the user lacks shows the no-access page, not a 403. */
export function requiredPermissionFor(pathname: string): string | null {
  const href = activeNavHref(pathname);
  if (!href) return null;
  const entry = NAV.find((e): e is NavEntry => 'href' in e && e.href === href);
  return entry ? entry.requires : null;
}

/**
 * The one nav entry a path belongs to: the entry whose **longest** covering
 * prefix wins.
 *
 * A plain `pathname.startsWith(href)` per entry was fine for as long as no nav
 * href was a prefix of another, and a detail route has no entry of its own: a
 * child route like `/analytics/carts` (the Live Baskets tab) or
 * `/orders/MM-…` must light its parent section, and where two entries nest the
 * more specific one has to win. A per-entry rule cannot express that; deciding
 * once, for the whole list, can.
 *
 * An entry may also declare a `match` prefix it OWNS beyond its own href — the
 * Reconciliation entry owns all of `/aggregators`, so a sibling tab like
 * `/aggregators/runs` (which has no sidebar entry) still lights it, while a
 * more-specific entry such as `/aggregators/invoices` overrides on its own page
 * because its covering prefix is longer. The winner is decided on the longest
 * covering prefix from EITHER the href or the match, and the entry's href is
 * returned (that is what the layout compares against).
 *
 * `/` is matched exactly, since it is a prefix of everything.
 */
export function activeNavHref(pathname: string, nav: typeof NAV = NAV): string | null {
  let best: string | null = null;
  let bestLen = -1;
  for (const entry of nav) {
    if ('section' in entry) continue;
    const prefixes = entry.match ? [entry.href, entry.match] : [entry.href];
    for (const p of prefixes) {
      const covers = p === '/' ? pathname === '/' : pathname === p || pathname.startsWith(`${p}/`);
      if (covers && p.length > bestLen) {
        best = entry.href;
        bestLen = p.length;
      }
    }
  }
  return best;
}
