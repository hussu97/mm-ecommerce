'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/lib/auth-context';
import { useCart } from '@/lib/cart-context';
import { ApiError, getSessionId } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';

export default function SignupPage() {
  const router = useRouter();
  const { register } = useAuth();
  const { mergeCart } = useCart();
  const { t } = useTranslation();

  const [form, setForm] = useState({
    email: '',
    password: '',
    confirmPassword: '',
    phone: '',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState('');

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
        phone: form.phone || undefined,
      });
      const sessionId = getSessionId();
      if (sessionId) await mergeCart(sessionId).catch(() => {});
      router.push('/account');
    } catch (err) {
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
          <Input
            label={t('settings.phone_optional')}
            type="tel"
            value={form.phone}
            onChange={e => setForm(f => ({ ...f, phone: e.target.value }))}
            error={errors.phone}
            placeholder={t('common.phone_placeholder')}
            autoComplete="tel"
          />
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
          <Button type="submit" fullWidth loading={loading} size="lg">
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
