'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { useAuth } from '@/lib/auth-context';
import { ApiError } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics } from '@/lib/analytics';

/**
 * Offered after the order is already placed, never before it.
 *
 * The moment someone has just bought something is the one moment an account is
 * obviously worth having — the address and the order are already there to keep.
 * Asking earlier is a wall in front of the purchase; asking here costs a
 * customer nothing if they ignore it, so it is a single password field and no
 * second thoughts about the order they have just completed.
 */
export function CreateAccountNudge({ email, phone }: { email: string; phone?: string }) {
  const { t } = useTranslation();
  const { register } = useAuth();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');

  if (done) {
    return (
      <div className="bg-green-50 border border-green-100 rounded-sm p-4 mb-6 flex gap-2 items-start">
        <span className="material-icons text-base text-green-600 mt-0.5">check_circle</span>
        <p className="font-body text-sm text-green-800">{t('confirmation.create_account_done')}</p>
      </div>
    );
  }

  const submit = async () => {
    if (password.length < 8) {
      setError(t('auth.password_min'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await register({ email, password, phone });
      analytics.userSignup();
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('auth.signup_failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-secondary/10 border border-secondary/30 rounded-sm p-4 mb-6">
      <div className="flex gap-2 items-start mb-3">
        <span className="material-icons text-base text-primary mt-0.5">bookmark_added</span>
        <div>
          <p className="font-body text-sm font-medium text-gray-800">
            {t('confirmation.create_account_title')}
          </p>
          <p className="font-body text-xs text-gray-500 mt-0.5">
            {t('confirmation.create_account_body')}
          </p>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row gap-2 sm:items-start">
        <div className="flex-1">
          <Input
            type="password"
            autoComplete="new-password"
            placeholder={t('confirmation.password_placeholder')}
            value={password}
            onChange={(e) => { setPassword(e.target.value); setError(''); }}
            onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
            error={error}
          />
          <p className="mt-1.5 text-xs text-gray-400 font-body truncate">{email}</p>
        </div>
        <Button variant="primary" onClick={submit} loading={busy} className="shrink-0">
          {t('confirmation.create_account_cta')}
        </Button>
      </div>
    </div>
  );
}
