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
// `match` lists extra path prefixes that also belong to an entry, for when one
// drawer item fronts a set of sibling routes that are not nested under its href
// — the Marketplaces entry lands on `/aggregators/reconciliation` but also owns
// `/aggregators/*` and `/grubops`, so all of its tabs light the same item.
export const NAV: Array<
  { href: string; label: string; icon: string; match?: string[] } | { section: string }
> = [
  { href: '/',              label: 'Dashboard',       icon: 'dashboard' },

  { section: 'Catalog' },
  { href: '/products',      label: 'Products',        icon: 'inventory_2' },
  { href: '/categories',    label: 'Categories',      icon: 'category' },
  { href: '/modifiers',     label: 'Modifiers',       icon: 'tune' },
  { href: '/menu-groups',   label: 'Menu Groups',     icon: 'account_tree' },

  // The transaction heart — every channel's orders, and the levers around a
  // sale. Counter orders live on the one Orders screen (the channels have
  // always shared a table); the register config lives under Operations.
  { section: 'Sales' },
  { href: '/orders',        label: 'Orders',          icon: 'receipt_long' },
  { href: '/custom-orders', label: 'Custom Orders',   icon: 'cake' },
  { href: '/customers',     label: 'Customers',        icon: 'people' },
  { href: '/promo-codes',   label: 'Promotions',      icon: 'local_offer' },
  // "Payment Gateways", not "Payments" — the storefront processors (Stripe,
  // Ziina), distinct from POS tender types under POS Config. Kept in a
  // top-visible group: it is the lever you reach for during a processor
  // incident, not the moment to go hunting three sections down.
  { href: '/payment-gateways', label: 'Payment Gateways', icon: 'credit_card' },

  // The whole aggregator relationship is now one tabbed screen —
  // reconciliation, item mappings, branch mappings, logins and GrubOps —
  // reached from a single drawer entry that lands on Reconciliation.
  { section: 'Marketplaces' },
  {
    href: '/aggregators/reconciliation',
    label: 'Marketplaces',
    icon: 'storefront',
    match: ['/aggregators', '/grubops'],
  },

  // The physical side: shops, tills, stock, and the config behind them.
  { section: 'Operations' },
  { href: '/branches',      label: 'Branches',        icon: 'storefront' },
  { href: '/devices',       label: 'Terminals',       icon: 'tablet_mac' },
  { href: '/staff',         label: 'Staff & Roles',   icon: 'badge' },
  { href: '/pos-config',    label: 'POS Config',      icon: 'settings_applications' },
  { href: '/inventory',     label: 'Inventory',       icon: 'warehouse' },
  { href: '/purchase-orders', label: 'Purchase Orders', icon: 'shopping_cart_checkout' },
  // Delivery zones and which kitchen bakes each one — fulfilment config that
  // sits with the branches it routes to.
  { href: '/delivery-zones', label: 'Delivery',       icon: 'local_shipping' },

  // All reporting in one place. Live Baskets is now a tab of Analytics (the
  // same funnel one step earlier), not its own drawer item; "Counter Reports"
  // is the trading-day counter reporting.
  { section: 'Reports' },
  { href: '/analytics',     label: 'Analytics',       icon: 'bar_chart' },
  { href: '/pos-reports',   label: 'Counter Reports', icon: 'insights' },

  // What a URL says and where it leads. Redirects sits by Content because it is
  // the same job — and it holds the URLs nothing else knows about, like one
  // from the old Wix site or a printed card.
  { section: 'Content' },
  { href: '/content',       label: 'Content',         icon: 'edit_note' },
  { href: '/redirects',     label: 'Redirects',       icon: 'alt_route' },

  { section: 'Settings & System' },
  { href: '/languages',     label: 'Languages',       icon: 'translate' },
  { href: '/translations',  label: 'Translations',    icon: 'text_fields' },
  { href: '/admin-users',   label: 'Admin Users',     icon: 'admin_panel_settings' },
  { href: '/import',        label: 'Import / Export', icon: 'sync_alt' },
  { href: '/security',      label: 'Security',        icon: 'vpn_key' },
  // Email, Webhook and Audit logs were three drawer items doing one job — "show
  // me what happened". One entry now, three tabs behind it (see `LogsTabs`); the
  // old paths redirect in.
  { href: '/logs',          label: 'Logs',            icon: 'article' },
];

/**
 * The one nav entry a path belongs to: the **longest** href that covers it.
 *
 * A plain `pathname.startsWith(href)` per entry was fine for as long as no nav
 * href was a prefix of another, and a detail route has no entry of its own: a
 * child route like `/analytics/carts` (the Live Baskets tab) or
 * `/orders/MM-…` must light its parent section, and where two entries nest the
 * more specific one has to win. A per-entry rule cannot express that; deciding
 * once, for the whole list, can.
 *
 * `/` is matched exactly, since it is a prefix of everything.
 */
export function activeNavHref(pathname: string, nav: typeof NAV = NAV): string | null {
  let best: { href: string; score: number } | null = null;
  for (const entry of nav) {
    if ('section' in entry) continue;
    // The entry's own href, plus any extra prefixes it declares it owns. The
    // longest one that covers the path decides specificity — so a match prefix
    // like `/aggregators` still yields to a more specific sibling href when one
    // exists, and the entry's own href wins on its canonical route.
    for (const prefix of [entry.href, ...(entry.match ?? [])]) {
      const covers =
        prefix === '/' ? pathname === '/' : pathname === prefix || pathname.startsWith(`${prefix}/`);
      if (covers && (best === null || prefix.length > best.score)) {
        best = { href: entry.href, score: prefix.length };
      }
    }
  }
  return best?.href ?? null;
}
