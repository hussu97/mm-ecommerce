'use client';

import Link from 'next/link';
import { useAuth } from '@/lib/auth-context';
import { useTranslation } from '@/lib/i18n/TranslationProvider';

export default function AccountPage() {
  const { user } = useAuth();
  const { t } = useTranslation();
  if (!user) return null;

  const QUICK_LINKS = [
    {
      href: '/account/orders',
      icon: 'receipt_long',
      label: t('account.my_orders'),
      description: t('account.my_orders_desc'),
    },
    {
      href: '/account/addresses',
      icon: 'location_on',
      label: t('account.addresses'),
      description: t('account.addresses_desc'),
    },
    {
      href: '/account/settings',
      icon: 'settings',
      label: t('account.settings'),
      description: t('account.settings_desc'),
    },
  ];

  return (
    <div>
      <div className="mb-8">
        <h1 className="font-display text-3xl text-primary mb-1">
          {t('account.hello', { name: user.first_name })}
        </h1>
        <p className="text-sm text-gray-500 font-body">
          {t('account.welcome')}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {QUICK_LINKS.map(({ href, icon, label, description }) => (
          <Link
            key={href}
            href={href}
            className="group border border-gray-200 hover:border-primary p-5 transition-colors"
          >
            <span className="material-icons text-3xl text-secondary group-hover:text-primary mb-3 block transition-colors">
              {icon}
            </span>
            <h2 className="text-sm font-medium uppercase tracking-widest text-gray-800 group-hover:text-primary transition-colors mb-1 font-body">
              {label}
            </h2>
            <p className="text-xs text-gray-500 font-body leading-relaxed">{description}</p>
          </Link>
        ))}
      </div>

      <div className="mt-8 p-4 bg-primary/5 border border-primary/20">
        <p className="text-xs text-gray-600 font-body">
          <span className="font-medium text-primary">{user.email}</span>
          {' · '}
          {t('account.member_since', {
            date: new Date(user.created_at).toLocaleDateString('en-AE', { month: 'long', year: 'numeric' }),
          })}
        </p>
      </div>
    </div>
  );
}
