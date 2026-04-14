'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/lib/auth-context';
import { cn } from '@/lib/utils';

const NAV = [
  { href: '/',              label: 'Dashboard',       icon: 'dashboard' },
  { href: '/products',      label: 'Products',        icon: 'inventory_2' },
  { href: '/categories',    label: 'Categories',      icon: 'category' },
  { href: '/modifiers',     label: 'Modifiers',       icon: 'tune' },
  { href: '/orders',        label: 'Orders',          icon: 'receipt_long' },
  { href: '/promo-codes',   label: 'Promo Codes',     icon: 'local_offer' },
  { href: '/customers',     label: 'Customers',       icon: 'people' },
  { href: '/analytics',     label: 'Analytics',       icon: 'bar_chart' },
  { href: '/import',        label: 'Import / Export', icon: 'sync_alt' },
  { href: '/languages',     label: 'Languages',       icon: 'translate' },
  { href: '/translations',  label: 'Translations',    icon: 'text_fields' },
  { href: '/content',       label: 'Content',         icon: 'edit_note' },
  { href: '/regions',       label: 'Regions',         icon: 'local_shipping' },
  { href: '/email-logs',    label: 'Email Logs',      icon: 'mail' },
  { href: '/audit-logs',    label: 'Audit Logs',      icon: 'manage_history' },
];

interface SidebarContentProps {
  collapsed: boolean;
  pathname: string;
  user: { email: string };
  setMobileOpen: (open: boolean) => void;
  onLogout: () => void;
}

function SidebarContent({ collapsed, pathname, user, setMobileOpen, onLogout }: SidebarContentProps) {
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
        {NAV.map(({ href, label, icon }) => {
          const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
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
              <span className="material-icons text-[18px]">{icon}</span>
              {!collapsed && <span className="text-xs font-body uppercase tracking-widest">{label}</span>}
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

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (!isLoading && !user) router.replace('/login');
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
        <header className="h-14 bg-white border-b border-gray-200 flex items-center px-4 gap-3 shrink-0">
          {/* Mobile hamburger */}
          <button
            onClick={() => setMobileOpen(o => !o)}
            className="md:hidden text-gray-500 hover:text-primary"
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

          <div className="flex items-center gap-2 text-xs font-body text-gray-500">
            <span className="material-icons text-[16px] text-primary">person</span>
            {user.email}
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 p-6 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
