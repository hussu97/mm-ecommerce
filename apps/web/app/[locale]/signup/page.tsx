'use client';

import { useState } from 'react';
import { Turnstile, isTurnstileEnabled } from '@/components/ui/Turnstile';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/lib/auth-context';
import { ApiError } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics, failureReason } from '@/lib/analytics';

export default function SignupPage() {
  const router = useRouter();
  const { register } = useAuth();
  const { t } = useTranslation();

  const [form, setForm] = useState({
    email: '',
    password: '',
    confirmPassword: '',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState('');
  // Empty until Cloudflare says this is a person, and empty again the moment
  // that answer expires — a spent solution is refused by the API.
  const [turnstileToken, setTurnstileToken] = useState('');

  function validate() {
    const e: Record<string, string> = {};
    if (!form.email) e.email = t('auth.email_required');
    if (!form.password) e.password = t('auth.password_required');
    else if (form.password.length < 8) e.password = t('auth.password_min_length');
    if (form.password !== form.confirmPassword) e.confirmPassword = t('auth.passwords_no_match');
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setLoading(true);
    setApiError('');
    try {
      await register({
        email: form.email,
        password: form.password,
        turnstile_token: turnstileToken || undefined,
      });
      analytics.userSignup({ surface: 'signup' });
      // No cart merge here: CartProvider watches the auth user and folds the
      // guest basket in on every signed-out → signed-in transition, wherever
      // it happens — see the effect in lib/cart-context.tsx.
      router.push('/account');
    } catch (err) {
      analytics.signupFailed({
        reason: failureReason(err),
        status: (err as { status?: number }).status,
      });
      setApiError(err instanceof ApiError ? err.message : t('auth.registration_failed'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-primary mb-2">{t('auth.create_account')}</h1>
          <p className="text-sm text-gray-500 font-body">{t('auth.create_account_subtitle')}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {apiError && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-3 rounded-sm">
              {apiError}
            </div>
          )}
          <Input
            label={t('common.email')}
            type="email"
            value={form.email}
            onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
            error={errors.email}
            autoComplete="email"
          />
          {/* No phone here. A number belongs to an address — it is who the
              driver rings when they cannot find that door — and asking for one
              at signup collected a second number that no order ever used, then
              left the customer wondering which of the two we would call. It is
              asked for once, on the address, where it is actually needed. */}
          <Input
            label={t('common.password')}
            type="password"
            value={form.password}
            onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
            error={errors.password}
            autoComplete="new-password"
            helper={t('auth.password_helper')}
          />
          <Input
            label={t('common.confirm_password')}
            type="password"
            value={form.confirmPassword}
            onChange={e => setForm(f => ({ ...f, confirmPassword: e.target.value }))}
            error={errors.confirmPassword}
            autoComplete="new-password"
          />
          <Turnstile onToken={setTurnstileToken} />
          <Button
            type="submit"
            fullWidth
            loading={loading}
            size="lg"
            /* Only where the check is switched on. With no site key there is
               no widget to solve and the button must not wait for one. */
            disabled={isTurnstileEnabled() && !turnstileToken}
          >
            {t('auth.create_account')}
          </Button>
        </form>

        <p className="text-center text-xs text-gray-400 mt-4 font-body">
          {t('auth.tos_text')}{' '}
          <Link href="/terms" className="underline">{t('auth.tos_terms')}</Link>{' '}
          {t('auth.tos_and')}{' '}
          <Link href="/privacy" className="underline">{t('auth.tos_privacy')}</Link>.
        </p>

        <p className="text-center text-sm text-gray-600 font-body mt-6">
          {t('auth.already_have_account')}{' '}
          <Link href="/login" className="text-primary hover:underline font-medium">
            {t('nav.sign_in')}
          </Link>
        </p>
      </div>
    </div>
  );
}
