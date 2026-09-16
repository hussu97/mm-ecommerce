'use client';

import { useCallback, useEffect, useState } from 'react';
import { startRegistration } from '@simplewebauthn/browser';
import { ApiError, authApi } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import type { AdminPasskey } from '@/lib/types';
import { Button, Input, LoadError, Spinner } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';
import { useConfirm } from '@/components/ui/feedback';
import { formatDate } from '@/lib/utils';

export default function SecurityPage() {
  const { user } = useAuth();
  const confirm = useConfirm();
  const [passkeys, setPasskeys] = useState<AdminPasskey[]>([]);
  const [name, setName] = useState('Admin passkey');
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  // The server's own verdict, not a guess from the address — the superadmin
  // account cannot hold passkeys, and hardcoding one email here went stale the
  // moment a second superadmin existed (F-ADM).
  const passkeysEnabled = !user?.is_superadmin;

  const loadPasskeys = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await authApi.passkeys();
      setPasskeys(rows);
      setLoadFailed(false);
    } catch {
      // A failed load is not "you have none" — that reading invites a second
      // enrolment on an account that already has one it just could not show.
      setPasskeys([]);
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPasskeys();
  }, [loadPasskeys]);

  async function addPasskey() {
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      const { options } = await authApi.passkeyRegistrationOptions();
      const credential = await startRegistration({ optionsJSON: options });
      await authApi.passkeyRegistrationVerify(credential, name.trim() || 'Admin passkey');
      setSuccess('Passkey added.');
      await loadPasskeys();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Passkey registration failed.');
    } finally {
      setSaving(false);
    }
  }

  async function deletePasskey(id: string) {
    if (
      !(await confirm({
        title: 'Remove this passkey?',
        message:
          'This device will no longer sign in with it. You can add it again later.',
        confirmLabel: 'Remove',
        danger: true,
      }))
    ) {
      return;
    }
    setError('');
    setSuccess('');
    try {
      await authApi.deletePasskey(id);
      setSuccess('Passkey removed.');
      await loadPasskeys();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove passkey.');
    }
  }

  return (
    <div className="max-w-3xl">
      <div className="mb-6">
        <h1 className="font-display text-2xl text-gray-800">Security</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">{user?.email}</p>
      </div>

      {error && (
        <div className="mb-4 border border-red-200 bg-red-50 px-3 py-2 text-xs font-body text-red-600">
          {error}
        </div>
      )}
      {success && (
        <div className="mb-4 border border-green-200 bg-green-50 px-3 py-2 text-xs font-body text-green-700">
          {success}
        </div>
      )}

      <div className="bg-white border border-gray-200 p-5 mb-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="font-display text-lg text-gray-800">Passkeys</h2>
            <p className="text-xs text-gray-400 font-body mt-1">
              {passkeysEnabled
                ? 'Add a passkey after password login to enable password or passkey sign-in. Works with Windows Hello, Touch ID, Face ID, and security keys.'
                : 'Passkeys are disabled for the superadmin account.'}
            </p>
          </div>
        </div>

        {passkeysEnabled && (
          <div className="mt-5 grid gap-3 sm:grid-cols-[1fr_auto]">
            <Input
              label="Passkey Name"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Windows Hello laptop"
            />
            <div className="sm:pt-6">
              <Button type="button" loading={saving} onClick={addPasskey}>
                Add Passkey
              </Button>
            </div>
          </div>
        )}
      </div>

      {loading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : loadFailed ? (
        <LoadError
          message="Could not load your passkeys."
          onRetry={() => void loadPasskeys()}
        />
      ) : (
        <DataTable<AdminPasskey>
          columns={[
            { header: 'Name', priority: 'primary', render: (p) => p.name ?? 'Admin passkey' },
            { header: 'Created', render: (p) => formatDate(p.created_at) },
            { header: 'Last Used', render: (p) => (p.last_used_at ? formatDate(p.last_used_at) : 'Never') },
          ]}
          rows={passkeys}
          rowKey={(p) => p.id}
          actions={(p) => <RowAction onClick={() => deletePasskey(p.id)}>Remove</RowAction>}
          empty={
            <p className="py-10 text-center text-sm text-gray-400 font-body">No passkeys registered.</p>
          }
        />
      )}
    </div>
  );
}
