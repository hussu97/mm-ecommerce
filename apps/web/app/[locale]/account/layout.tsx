'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/lib/auth-context';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { cn } from '@/lib/utils';

export default function AccountLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const { t } = useTranslation();

  const NAV_ITEMS = [
    { href: '/account',           label: t('account.my_profile'),  icon: 'person' },
    { href: '/account/orders',    label: t('account.my_orders'),   icon: 'receipt_long' },
    { href: '/account/addresses', label: t('account.addresses'),   icon: 'location_on' },
    { href: '/account/settings',  label: t('account.settings'),    icon: 'settings' },
  ];

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace(`/login?redirect=${pathname}`);
    }
  }, [isLoading, user, router, pathname]);

  if (isLoading) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-gray-400 font-body uppercase tracking-widest">{t('common.loading')}</p>
        </div>
      </div>
    );
  }

  if (!user) return null;

  function handleLogout() {
    logout();
    router.push('/');
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <div className="flex flex-col md:flex-row gap-8">
        {/* Sidebar */}
        <aside className="md:w-56 shrink-0">
          <div className="mb-5 px-1">
            <p className="text-xs text-gray-400 uppercase tracking-widest font-body mb-0.5">{t('account.signed_in_as')}</p>
            <p className="text-sm font-medium text-gray-800 font-body truncate">{user.first_name} {user.last_name}</p>
          </div>

          <nav className="space-y-0.5">
            {NAV_ITEMS.map(({ href, label, icon }) => {
              const active = pathname === href;
              return (
                <Link
                  key={href}
                  href={href}
                  className={cn(
                    'flex items-center gap-3 px-4 py-3 text-sm font-body transition-colors',
                    active
                      ? 'bg-primary/10 text-primary border-l-2 border-primary'
                      : 'text-gray-600 hover:bg-gray-50 hover:text-primary border-l-2 border-transparent',
                  )}
                >
                  <span className="material-icons text-[18px]">{icon}</span>
                  {label}
                </Link>
              );
            })}
          </nav>

          <div className="mt-4 pt-4 border-t border-gray-100">
            <button
              onClick={handleLogout}
              className="flex items-center gap-3 px-4 py-3 w-full text-sm text-gray-500 hover:text-red-500 font-body transition-colors"
            >
              <span className="material-icons text-[18px]">logout</span>
              {t('nav.sign_out')}
            </button>
          </div>
        </aside>

        {/* Content */}
        <main className="flex-1 min-w-0">
          {children}
        </main>
      </div>
    </div>
  );
}
