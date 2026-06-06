'use client';

import { useCallback, useEffect, useState } from 'react';
import { startRegistration } from '@simplewebauthn/browser';
import { ApiError, authApi } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import type { AdminPasskey } from '@/lib/types';
import { Button, Input } from '@/components/ui';
import { formatDate } from '@/lib/utils';

const SUPERADMIN_EMAIL = 'admin@meltingmomentscakes.com';

export default function SecurityPage() {
  const { user } = useAuth();
  const [passkeys, setPasskeys] = useState<AdminPasskey[]>([]);
  const [name, setName] = useState('Admin passkey');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const passkeysEnabled = user?.email !== SUPERADMIN_EMAIL;

  const loadPasskeys = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await authApi.passkeys();
      setPasskeys(rows);
    } catch {
      setPasskeys([]);
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
                ? 'Add a passkey after password login to enable password or passkey sign-in.'
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
              placeholder="MacBook Touch ID"
            />
            <div className="sm:pt-6">
              <Button type="button" loading={saving} onClick={addPasskey}>
                Add Passkey
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="bg-white border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Name</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Created</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Last Used</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan={4} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  Loading…
                </td>
              </tr>
            ) : passkeys.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  No passkeys registered.
                </td>
              </tr>
            ) : (
              passkeys.map(passkey => (
                <tr key={passkey.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3">
                    <span className="text-xs font-body font-medium text-gray-800">{passkey.name ?? 'Admin passkey'}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs font-body text-gray-500">{formatDate(passkey.created_at)}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs font-body text-gray-500">
                      {passkey.last_used_at ? formatDate(passkey.last_used_at) : 'Never'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button variant="ghost" size="sm" onClick={() => deletePasskey(passkey.id)}>
                      Remove
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
