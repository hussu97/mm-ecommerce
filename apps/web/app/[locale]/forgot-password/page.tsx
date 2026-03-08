'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { authApi, ApiError } from '@/lib/api';
import { useTranslation } from '@/lib/i18n/TranslationProvider';

export default function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) { setError(t('auth.email_required')); return; }
    setLoading(true);
    setError('');
    try {
      await authApi.forgotPassword(email);
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
        <div className="w-full max-w-md text-center">
          <span className="material-icons text-5xl text-primary mb-4 block">mark_email_read</span>
          <h1 className="font-display text-3xl text-primary mb-3">{t('auth.check_email_title')}</h1>
          <p className="text-sm text-gray-600 font-body mb-6">
            {t('auth.check_email_body', { email })}
          </p>
          <Link href="/login" className="text-sm text-primary hover:underline font-body">
            {t('auth.back_to_sign_in')}
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-primary mb-2">{t('auth.reset_password')}</h1>
          <p className="text-sm text-gray-500 font-body">
            {t('auth.reset_subtitle')}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-3 rounded-sm">
              {error}
            </div>
          )}
          <Input
            label={t('common.email')}
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            autoComplete="email"
          />
          <Button type="submit" fullWidth loading={loading} size="lg">
            {t('auth.send_reset_link')}
          </Button>
        </form>

        <p className="text-center text-sm text-gray-600 font-body mt-6">
          <Link href="/login" className="text-primary hover:underline">
            {t('auth.back_to_sign_in')}
          </Link>
        </p>
      </div>
    </div>
  );
}
