'use client';

import Link from 'next/link';
import { useAuth } from '@/lib/auth-context';

const QUICK_LINKS = [
  {
    href: '/account/orders',
    icon: 'receipt_long',
    label: 'My Orders',
    description: 'Track and view your past orders',
  },
  {
    href: '/account/addresses',
    icon: 'location_on',
    label: 'Addresses',
    description: 'Manage your saved delivery addresses',
  },
  {
    href: '/account/settings',
    icon: 'settings',
    label: 'Settings',
    description: 'Edit your profile and password',
  },
];

export default function AccountPage() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <div>
      <div className="mb-8">
        <h1 className="font-display text-3xl text-primary mb-1">
          Hello, {user.first_name}
        </h1>
        <p className="text-sm text-gray-500 font-body">
          Welcome to your Melting Moments account.
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
          {' · '}Member since {new Date(user.created_at).toLocaleDateString('en-AE', { month: 'long', year: 'numeric' })}
        </p>
      </div>
    </div>
  );
}
