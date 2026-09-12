'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/lib/auth-context';
import { loginPathFor } from '@/lib/auth-redirect';
import { activeNavHref, canAccessNav, NAV, requiredPermissionFor } from '@/lib/nav';
import { DensityToggle } from '@/components/ui/DensityToggle';
import { cn } from '@/lib/utils';

interface SidebarContentProps {
  collapsed: boolean;
  pathname: string;
  user: { email: string; permissions: string[]; is_superadmin: boolean };
  setMobileOpen: (open: boolean) => void;
  onLogout: () => void;
}

function SidebarContent({ collapsed, pathname, user, setMobileOpen, onLogout }: SidebarContentProps) {
  const currentHref = activeNavHref(pathname);
  // Show only the screens this user's API access would actually serve, and drop
  // a section heading once all its entries are gone (F-ADM-7).
  const allowed = NAV.filter((e) => 'section' in e || canAccessNav(e, user));
  const nav = allowed.filter((e, i) => {
    if (!('section' in e)) return true;
    const next = allowed[i + 1];
    return next !== undefined && !('section' in next);
  });
  return (
    <>
      {/* Logo */}
      <div className={cn('flex items-center h-14 px-4 border-b border-gray-100', collapsed ? 'justify-center' : 'gap-3')}>
        <span className="material-icons text-primary text-xl">storefront</span>
        {!collapsed && (
          <span className="font-display text-sm text-primary tracking-widest uppercase">MM Admin</span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 py-4 overflow-y-auto">
        {nav.map((entry) => {
          if ('section' in entry) {
            // Section headings disappear when collapsed — a lone divider reads
            // better than a truncated word in a 56px rail.
            return collapsed ? (
              <div key={entry.section} className="my-2 mx-3 border-t border-gray-100" />
            ) : (
              <p
                key={entry.section}
                className="px-4 pt-4 pb-1 text-[10px] uppercase tracking-widest text-gray-400 font-body"
              >
                {entry.section}
              </p>
            );
          }
          const { href, label, icon } = entry;
          const active = href === currentHref;
          return (
            <Link
              key={href}
              href={href}
              onClick={() => setMobileOpen(false)}
              title={collapsed ? label : undefined}
              className={cn(
                'flex items-center h-10 transition-colors',
                collapsed ? 'justify-center px-0' : 'gap-3 px-4',
                active
                  ? 'bg-primary/10 text-primary border-r-2 border-primary'
                  : 'text-gray-600 hover:bg-gray-50 hover:text-primary border-r-2 border-transparent',
              )}
            >
              <span className="material-icons text-[18px] shrink-0">{icon}</span>
              {/* `min-w-0` + `truncate`: "Purchase Orders" and "Import /
                  Export" are wider than the 208px rail once the icon and the
                  uppercase tracking are paid for, and without these the whole
                  nav became a horizontal scroller nobody would ever find. */}
              {!collapsed && (
                <span className="min-w-0 truncate text-xs font-body uppercase tracking-widest">
                  {label}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* User + Logout */}
      <div className={cn('border-t border-gray-100 py-3', collapsed ? 'px-0' : 'px-4')}>
        {!collapsed && (
          <p className="text-[11px] text-gray-400 font-body mb-2 truncate">
            {user.email}
          </p>
        )}
        <button
          onClick={onLogout}
          title={collapsed ? 'Sign out' : undefined}
          className={cn(
            'flex items-center gap-2 text-xs text-gray-400 hover:text-red-500 font-body transition-colors',
            collapsed ? 'justify-center w-full' : '',
          )}
        >
          <span className="material-icons text-[16px]">logout</span>
          {!collapsed && 'Sign out'}
        </button>
      </div>
    </>
  );
}

function NoAccessScreen() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <span className="material-icons text-4xl text-gray-300 mb-3">lock</span>
      <h1 className="font-display text-xl text-gray-800 mb-1">No access to this screen</h1>
      <p className="max-w-sm text-sm font-body text-gray-500">
        Your role doesn&rsquo;t include permission for this page. Ask an administrator
        to add it to your role if you need it.
      </p>
      <Link
        href="/"
        className="mt-5 text-xs font-body uppercase tracking-widest text-primary hover:underline"
      >
        Back to dashboard
      </Link>
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace(loginPathFor(window.location.pathname, window.location.search));
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-7 h-7 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }
  if (!user) return null;

  function handleLogout() {
    logout();
    router.push('/login');
  }

  // The screen at this path is off-limits when its nav entry names a slug the
  // user lacks — enforced here (not just hidden from the sidebar) so a deep link
  // or a bookmark lands on a clear no-access page rather than a 403-driven mess.
  const needed = requiredPermissionFor(pathname);
  const canView = needed === null || user.is_superadmin || user.permissions.includes(needed);

  return (
    <div className="flex min-h-screen">
      {/* Desktop Sidebar */}
      <aside
        className={cn(
          'hidden md:flex flex-col bg-white border-r border-gray-200 shrink-0 transition-all duration-200',
          collapsed ? 'w-14' : 'w-52',
        )}
      >
        <SidebarContent
          collapsed={collapsed}
          pathname={pathname}
          user={user}
          setMobileOpen={setMobileOpen}
          onLogout={handleLogout}
        />
      </aside>

      {/* Mobile Sidebar */}
      {mobileOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/30"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="fixed top-0 left-0 z-50 w-52 h-full bg-white border-r border-gray-200 flex flex-col">
            <SidebarContent
              collapsed={collapsed}
              pathname={pathname}
              user={user}
              setMobileOpen={setMobileOpen}
              onLogout={handleLogout}
            />
          </aside>
        </>
      )}

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="h-14 bg-white border-b border-gray-200 flex items-center px-2 md:px-4 gap-2 md:gap-3 shrink-0">
          {/* Mobile hamburger. Square and full-height rather than a bare icon:
              this is the only way to the navigation on a phone, and an 18px
              glyph is not a target a thumb can rely on. */}
          <button
            onClick={() => setMobileOpen(o => !o)}
            aria-label="Open navigation"
            className="md:hidden flex items-center justify-center min-w-[var(--tap-min)] min-h-[var(--tap-min)] -ml-1 text-gray-500 hover:text-primary"
          >
            <span className="material-icons">menu</span>
          </button>

          {/* Desktop collapse toggle */}
          <button
            onClick={() => setCollapsed(c => !c)}
            className="hidden md:block text-gray-400 hover:text-primary transition-colors"
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <span className="material-icons text-[18px]">
              {collapsed ? 'chevron_right' : 'chevron_left'}
            </span>
          </button>

          <div className="flex-1" />

          <DensityToggle />

          {/* `min-w-0` + `truncate`: an admin's address can be sixty
              characters, and without both of these it pushed the hamburger
              off the left edge of a phone rather than shortening itself. */}
          <div className="flex items-center gap-2 min-w-0 text-xs font-body text-gray-500">
            <span className="material-icons text-[16px] text-primary shrink-0">person</span>
            <span className="truncate">{user.email}</span>
          </div>
        </header>

        {/* Page Content.
            The single source of the page gutter — `ResourcePage` used to add
            its own `p-6` on top of this one, so half the console was drawn in
            294px of a 390px screen. Anything rendered here supplies content,
            never padding. */}
        <main className="flex-1 px-4 py-5 md:p-6 overflow-y-auto overflow-x-hidden">
          {canView ? children : <NoAccessScreen />}
        </main>
      </div>
    </div>
  );
}
