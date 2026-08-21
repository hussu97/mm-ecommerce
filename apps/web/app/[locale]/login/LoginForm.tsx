'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/lib/auth-context';
import { ApiError } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics, failureReason } from '@/lib/analytics';

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login } = useAuth();
  const { t } = useTranslation();

  const [form, setForm] = useState({ email: '', password: '' });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState('');

  function validate() {
    const e: Record<string, string> = {};
    if (!form.email) e.email = t('auth.email_required');
    if (!form.password) e.password = t('auth.password_required');
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setLoading(true);
    setApiError('');
    try {
      await login(form.email, form.password);
      analytics.userLogin();
      // No cart merge here: CartProvider watches the auth user and folds the
      // guest basket in on every signed-out → signed-in transition, wherever
      // it happens. This page remembering to do it was the only reason it
      // worked at all — see the effect in lib/cart-context.tsx.
      const redirect = searchParams.get('redirect') || '/account';
      router.push(redirect);
    } catch (err) {
      // Wrong password, locked account and a 500 all showed the customer one
      // red box and told us nothing. They are three different problems and only
      // one of them is theirs.
      analytics.loginFailed({
        reason: failureReason(err),
        status: (err as { status?: number }).status,
      });
      setApiError(err instanceof ApiError ? err.message : t('auth.login_failed'));
    } finally {
      setLoading(false);
    }
  }
  return (
    <>
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
        <div>
          <Input
            label={t('common.password')}
            type="password"
            value={form.password}
            onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
            error={errors.password}
            autoComplete="current-password"
          />
          <div className="flex justify-end mt-1.5">
            <Link href="/forgot-password" className="text-xs text-primary hover:underline font-body">
              {t('auth.forgot_password_link')}
            </Link>
          </div>
        </div>
        <Button type="submit" fullWidth loading={loading} size="lg">
          {t('nav.sign_in')}
        </Button>
      </form>

      <div className="mt-8 space-y-3">
        <div className="relative">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-gray-200" />
          </div>
          <div className="relative flex justify-center">
            <span className="bg-white px-3 text-xs text-gray-400 uppercase tracking-widest">{t('auth.or_divider')}</span>
          </div>
        </div>
        <p className="text-center text-sm text-gray-600 font-body">
          {t('auth.no_account')}{' '}
          <Link href="/signup" className="text-primary hover:underline font-medium">
            {t('auth.sign_up_link')}
          </Link>
        </p>
        <p className="text-center text-sm text-gray-600 font-body">
          {t('auth.just_browsing')}{' '}
          <Link href="/" className="text-primary hover:underline font-medium">
            {t('auth.continue_as_guest')}
          </Link>
        </p>
      </div>
    </>
  );
}
