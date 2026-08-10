'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { useAuth } from '@/lib/auth-context';
import { addressesApi, ApiError } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics } from '@/lib/analytics';
import type { AddressCreate } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

/**
 * Offered after the order is already placed, never before it.
 *
 * The moment someone has just bought something is the one moment an account is
 * obviously worth having — the address and the order are already there to keep.
 * Asking earlier is a wall in front of the purchase; asking here costs a
 * customer nothing if they ignore it.
 *
 * Which question gets asked depends on whether the email already belongs to an
 * account. Telling a returning customer to "create an account" with the address
 * they have used for a year is a dead end: the form is the right shape but the
 * only outcome is "that email is taken", and the customer is left holding an
 * error for having done nothing wrong. So an email we recognise is asked to
 * sign in instead, and in both cases the address they just ordered to is saved
 * to the account afterwards — which is the part they actually gain.
 */
export function CreateAccountNudge({
  email,
  phone,
  hasAccount = false,
  address,
}: {
  email: string;
  phone?: string;
  /** The order's email is already a real, signable-in account. */
  hasAccount?: boolean;
  /** The address this order went to, saved once there is an account to hang it on. */
  address?: AddressCreate | null;
}) {
  const { t } = useTranslation();
  const { login, register } = useAuth();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [addressSaved, setAddressSaved] = useState(false);
  const [error, setError] = useState('');

  if (done) {
    return (
      <div className="bg-green-50 border border-green-100 rounded-sm p-4 mb-6 flex gap-2 items-start">
        <Icon name="check_circle" className="text-base text-green-600 mt-0.5" />
        <p className="font-body text-sm text-green-800">
          {addressSaved
            ? t(hasAccount ? 'confirmation.signed_in_address_saved' : 'confirmation.create_account_address_saved')
            : t(hasAccount ? 'confirmation.signed_in_done' : 'confirmation.create_account_done')}
        </p>
      </div>
    );
  }

  /**
   * Keep the address, now that there is somewhere to keep it.
   *
   * Deliberately never fails the flow it is attached to. The customer asked to
   * sign in; they are signed in. A duplicate address, or an address the account
   * already has, is not something to show them an error about after the thing
   * they wanted has already worked.
   */
  const saveAddress = async () => {
    if (!address) return;
    try {
      await addressesApi.create(address);
      setAddressSaved(true);
    } catch {
      /* signed in either way — the address is still on the order */
    }
  };

  const submit = async () => {
    if (!hasAccount && password.length < 8) {
      setError(t('auth.password_min'));
      return;
    }
    if (!password) {
      setError(t('auth.password_required'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      if (hasAccount) {
        await login(email, password);
      } else {
        await register({ email, password, phone });
        analytics.userSignup();
      }
      await saveAddress();
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : t(hasAccount ? 'auth.login_failed' : 'auth.signup_failed'),
      );
    } finally {
      setBusy(false);
    }
  };

  const body = address
    ? t(hasAccount ? 'confirmation.sign_in_body_address' : 'confirmation.create_account_body_address')
    : t(hasAccount ? 'confirmation.sign_in_body' : 'confirmation.create_account_body');

  return (
    <div className="bg-secondary/10 border border-secondary/30 rounded-sm p-4 mb-6">
      <div className="flex gap-2 items-start mb-3">
        <Icon name={hasAccount ? 'login' : 'bookmark_added'} className="text-base text-primary mt-0.5" />
        <div>
          <p className="font-body text-sm font-medium text-gray-800">
            {t(hasAccount ? 'confirmation.sign_in_title' : 'confirmation.create_account_title')}
          </p>
          <p className="font-body text-xs text-gray-500 mt-0.5">{body}</p>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row gap-2 sm:items-start">
        <div className="flex-1">
          <Input
            type="password"
            autoComplete={hasAccount ? 'current-password' : 'new-password'}
            placeholder={t(
              hasAccount ? 'confirmation.sign_in_password_placeholder' : 'confirmation.password_placeholder',
            )}
            value={password}
            onChange={(e) => { setPassword(e.target.value); setError(''); }}
            onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
            error={error}
          />
          {/* The email the account would be created under, printed in full.
              Masked out of Clarity recordings — the password field above it is
              masked by Clarity in every mode, and it would be a poor showing to
              hide the password and keep the address it belongs to. */}
          <p className="mt-1.5 text-xs text-gray-400 font-body truncate" data-clarity-mask="true">{email}</p>
        </div>
        <Button variant="primary" onClick={submit} loading={busy} className="shrink-0">
          {t(hasAccount ? 'confirmation.sign_in_cta' : 'confirmation.create_account_cta')}
        </Button>
      </div>
    </div>
  );
}
