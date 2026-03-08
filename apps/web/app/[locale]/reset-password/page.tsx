'use client';

import { Suspense, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { authApi, ApiError } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get('token') || '';
  const { t } = useTranslation();

  const [form, setForm] = useState({ password: '', confirm: '' });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [apiError, setApiError] = useState('');

  if (!token) {
    return (
      <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
        <div className="w-full max-w-md text-center">
          <span className="material-icons text-5xl text-red-400 mb-4 block">error_outline</span>
          <h1 className="font-display text-2xl text-primary mb-3">{t('auth.invalid_link')}</h1>
          <p className="text-sm text-gray-500 font-body mb-6">
            {t('auth.invalid_link_body')}
          </p>
          <Link href="/forgot-password" className="text-sm text-primary hover:underline font-body">
            {t('auth.request_new_link')}
          </Link>
        </div>
      </div>
    );
  }

  function validate() {
    const e: Record<string, string> = {};
    if (!form.password) e.password = t('auth.password_required');
    else if (form.password.length < 8) e.password = t('auth.password_min_length');
    if (form.password !== form.confirm) e.confirm = t('auth.passwords_no_match');
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setLoading(true);
    setApiError('');
    try {
      await authApi.resetPassword(token, form.password);
      setDone(true);
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : t('auth.reset_failed'));
    } finally {
      setLoading(false);
    }
  }

  if (done) {
    return (
      <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
        <div className="w-full max-w-md text-center">
          <span className="material-icons text-5xl text-green-500 mb-4 block">check_circle</span>
          <h1 className="font-display text-3xl text-primary mb-3">{t('auth.password_updated')}</h1>
          <p className="text-sm text-gray-600 font-body mb-6">
            {t('auth.password_updated_body')}
          </p>
          <Link href="/login">
            <Button size="lg">{t('nav.sign_in')}</Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-primary mb-2">{t('auth.set_new_password')}</h1>
          <p className="text-sm text-gray-500 font-body">{t('auth.set_password_subtitle')}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {apiError && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-3 rounded-sm">
              {apiError}
            </div>
          )}
          <Input
            label={t('auth.new_password')}
            type="password"
            value={form.password}
            onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
            error={errors.password}
            helper={t('auth.password_helper')}
            autoComplete="new-password"
          />
          <Input
            label={t('common.confirm_password')}
            type="password"
            value={form.confirm}
            onChange={e => setForm(f => ({ ...f, confirm: e.target.value }))}
            error={errors.confirm}
            autoComplete="new-password"
          />
          <Button type="submit" fullWidth loading={loading} size="lg">
            {t('auth.update_password')}
          </Button>
        </form>
      </div>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetPasswordForm />
    </Suspense>
  );
}
