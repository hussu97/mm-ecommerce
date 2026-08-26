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
export const NAV: Array<
  { href: string; label: string; icon: string } | { section: string }
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

  // The aggregator relationship, in one place at last: what we push to the
  // marketplaces (GrubOps), what they paid us back (Reconciliation), and which
  // of our branches each outlet id maps to (Mappings). Three sides of one
  // relationship that used to be scattered through "Online store".
  { section: 'Marketplaces' },
  { href: '/grubops',       label: 'GrubOps',         icon: 'restaurant_menu' },
  { href: '/aggregators/reconciliation', label: 'Reconciliation', icon: 'account_balance' },
  { href: '/aggregators/mappings', label: 'Mappings', icon: 'link' },

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
  { href: '/email-logs',    label: 'Email Logs',      icon: 'mail' },
  { href: '/webhook-logs',  label: 'Webhook Logs',    icon: 'webhook' },
  { href: '/audit-logs',    label: 'Audit Logs',      icon: 'manage_history' },
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
  let best: string | null = null;
  for (const entry of nav) {
    if ('section' in entry) continue;
    const { href } = entry;
    const covers = href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(`${href}/`);
    if (covers && (best === null || href.length > best.length)) best = href;
  }
  return best;
}
