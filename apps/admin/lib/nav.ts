/**
 * The console's navigation, and the rule for which entry is lit.
 *
 * Its own module rather than a pair of exports from `layout.tsx`: a route file
 * is meant to export its default and Next's own configuration keys, and a
 * helper hanging off one is a helper that a framework upgrade is entitled to
 * complain about. It is also the only way to test the rule below without
 * mounting a layout.
 */

// Grouped so the POS surface stays distinguishable from the storefront one as
// the navigation grows.
export const NAV: Array<
  { href: string; label: string; icon: string } | { section: string }
> = [
  { href: '/',              label: 'Dashboard',       icon: 'dashboard' },

  { section: 'Catalogue' },
  { href: '/products',      label: 'Products',        icon: 'inventory_2' },
  { href: '/categories',    label: 'Categories',      icon: 'category' },
  { href: '/modifiers',     label: 'Modifiers',       icon: 'tune' },
  { href: '/menu-groups',   label: 'Menu Groups',     icon: 'account_tree' },

  { section: 'Point of Sale' },
  // Counter orders live on the one Orders screen now — the two channels have
  // always shared a table, and two entries here meant counting today's takings
  // twice and adding them up.
  { href: '/branches',      label: 'Branches',        icon: 'storefront' },
  { href: '/staff',         label: 'Staff & Roles',   icon: 'badge' },
  { href: '/devices',       label: 'Devices',         icon: 'tablet_mac' },
  { href: '/pos-config',    label: 'POS Config',      icon: 'settings_applications' },
  { href: '/pos-reports',   label: 'POS Reports',     icon: 'insights' },

  { section: 'Inventory' },
  { href: '/inventory',     label: 'Inventory',       icon: 'warehouse' },
  { href: '/purchase-orders', label: 'Purchase Orders', icon: 'shopping_cart_checkout' },

  { section: 'Online store' },
  { href: '/orders',        label: 'Orders',          icon: 'receipt_long' },
  { href: '/custom-orders', label: 'Custom Orders',   icon: 'cake' },
  { href: '/promo-codes',   label: 'Promo Codes',     icon: 'local_offer' },
  { href: '/customers',     label: 'Customers',       icon: 'people' },
  { href: '/delivery-zones', label: 'Delivery',       icon: 'local_shipping' },
  // Under "Online store" and not "System": this is the lever you reach for
  // during a processor incident, and an incident is not the moment to go
  // hunting three sections down.
  { href: '/payment-gateways', label: 'Payments',     icon: 'credit_card' },
  { href: '/analytics',     label: 'Analytics',       icon: 'bar_chart' },
  // Its own entry rather than a section of Analytics: everything on that screen
  // is a date range over `orders`, and this is the live present. Sat next to it
  // because it is the same question asked one step earlier in the funnel.
  { href: '/analytics/carts', label: 'Live Baskets',  icon: 'shopping_basket' },
  { href: '/content',       label: 'Content',         icon: 'edit_note' },
  // Beside Content because it is the same job: what a URL says and where it
  // leads. Renaming a category writes its own rule here, so this screen is
  // mostly somewhere to look — and the place to add the ones nothing else knows
  // about, like a URL from the Wix site or a printed card.
  { href: '/redirects',     label: 'Redirects',       icon: 'alt_route' },
  { href: '/languages',     label: 'Languages',       icon: 'translate' },
  { href: '/translations',  label: 'Translations',    icon: 'text_fields' },

  { section: 'System' },
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
 * href was a prefix of another. `/analytics` and `/analytics/carts` are, and
 * under the old rule standing on Live Baskets lit up Analytics as well — two
 * highlighted entries, neither of them wrong enough to be obviously a bug and
 * both of them wrong. Deciding once, for the whole list, cannot express that
 * state.
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
