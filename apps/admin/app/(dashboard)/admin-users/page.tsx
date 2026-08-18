'use client';

import { useEffect, useState } from 'react';
import { adminUsersApi } from '@/lib/api';
import type { AdminUserSummary } from '@/lib/types';
import { formatDate } from '@/lib/utils';
import { Badge, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUserSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminUsersApi.list()
      .then(setUsers)
      .catch(() => setUsers([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-[1400px]">
      <div className="mb-6">
        <h1 className="font-display text-2xl text-gray-800">Admin Users</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">{users.length} admins</p>
      </div>

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<AdminUserSummary>
          rows={users}
          rowKey={u => u.id}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">
              No admin users found.
            </p>
          }
          columns={[
            {
              header: 'Email',
              // The identity of the row, and on a phone the card's title. An
              // admin address is long, so it gets the full width rather than a
              // fifth of it.
              priority: 'primary',
              render: u => (
                <span className="text-xs font-body font-medium text-gray-800 break-all">
                  {u.email}
                </span>
              ),
            },
            {
              header: 'Role',
              render: u => (
                <Badge variant={u.is_superadmin ? 'info' : 'neutral'}>
                  {u.is_superadmin ? 'Superadmin' : 'Admin'}
                </Badge>
              ),
            },
            {
              header: 'Passkeys',
              className: 'text-center',
              render: u => (u.is_superadmin ? 'Disabled' : u.passkey_count),
            },
            {
              header: 'Status',
              className: 'text-center',
              render: u => (
                <span className={u.is_active ? 'text-green-600' : 'text-red-500'}>
                  {u.is_active ? 'Active' : 'Inactive'}
                </span>
              ),
            },
            {
              header: 'Created',
              className: 'text-right',
              render: u => <span className="text-gray-400">{formatDate(u.created_at)}</span>,
            },
          ]}
        />
      )}
    </div>
  );
}
