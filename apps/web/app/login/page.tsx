'use client';

import { Suspense, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/lib/auth-context';
import { useCart } from '@/lib/cart-context';
import { ApiError, getSessionId } from '@/lib/api';

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login } = useAuth();
  const { mergeCart } = useCart();

  const [form, setForm] = useState({ email: '', password: '' });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState('');

  function validate() {
    const e: Record<string, string> = {};
    if (!form.email) e.email = 'Email is required';
    if (!form.password) e.password = 'Password is required';
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
      const sessionId = getSessionId();
      if (sessionId) await mergeCart(sessionId).catch(() => {});
      const redirect = searchParams.get('redirect') || '/account';
      router.push(redirect);
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Login failed. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-primary mb-2">Welcome Back</h1>
          <p className="text-sm text-gray-500 font-body">Sign in to your Melting Moments account</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {apiError && (
            <div className="bg-red-50 border border-red-200 text-red-600 text-sm px-4 py-3 rounded-sm">
              {apiError}
            </div>
          )}
          <Input
            label="Email"
            type="email"
            value={form.email}
            onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
            error={errors.email}
            autoComplete="email"
          />
          <div>
            <Input
              label="Password"
              type="password"
              value={form.password}
              onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
              error={errors.password}
              autoComplete="current-password"
            />
            <div className="flex justify-end mt-1.5">
              <Link href="/forgot-password" className="text-xs text-primary hover:underline font-body">
                Forgot password?
              </Link>
            </div>
          </div>
          <Button type="submit" fullWidth loading={loading} size="lg">
            Sign In
          </Button>
        </form>

        <div className="mt-8 space-y-3">
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-gray-200" />
            </div>
            <div className="relative flex justify-center">
              <span className="bg-white px-3 text-xs text-gray-400 uppercase tracking-widest">or</span>
            </div>
          </div>
          <p className="text-center text-sm text-gray-600 font-body">
            Don&apos;t have an account?{' '}
            <Link href="/signup" className="text-primary hover:underline font-medium">
              Sign up
            </Link>
          </p>
          <p className="text-center text-sm text-gray-600 font-body">
            Just browsing?{' '}
            <Link href="/" className="text-primary hover:underline font-medium">
              Continue as guest
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
