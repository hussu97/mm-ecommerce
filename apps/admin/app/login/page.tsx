'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  WebAuthnAbortService,
  browserSupportsWebAuthn,
  browserSupportsWebAuthnAutofill,
  startAuthentication,
} from '@simplewebauthn/browser';
import { useAuth } from '@/lib/auth-context';
import { ApiError, authApi } from '@/lib/api';
import { Button, Input } from '@/components/ui';
import type { AdminLoginOptions } from '@/lib/types';
import { safeNextPath } from '@/lib/auth-redirect';

/** The person closed or dismissed the passkey prompt (or a newer one replaced it). */
function isPasskeyCancel(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  const code = (err as { code?: string }).code;
  return err.name === 'NotAllowedError' || err.name === 'AbortError' || code === 'ERROR_CEREMONY_ABORTED';
}

export default function LoginPage() {
  const router = useRouter();
  const { login, loginWithPasskey } = useAuth();

  const [form, setForm] = useState({ email: '', password: '' });
  const [step, setStep] = useState<'email' | 'auth'>('email');
  const [loginOptions, setLoginOptions] = useState<AdminLoginOptions | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [passkeyLoading, setPasskeyLoading] = useState(false);
  const [apiError, setApiError] = useState('');
  // Decided after mount: the server render has no `window` to ask.
  const [passkeysSupported, setPasskeysSupported] = useState(false);

  useEffect(() => {
    setPasskeysSupported(browserSupportsWebAuthn());
  }, []);

  // Passkey autofill on the email field, where the browser supports it: the
  // saved passkeys show up in the field's suggestions, no email needed. A
  // click on "Sign in with a passkey" replaces this pending request.
  useEffect(() => {
    if (step !== 'email') return;
    let live = true;
    void (async () => {
      let credential: unknown;
      try {
        if (!(await browserSupportsWebAuthnAutofill()) || !live) return;
        const { options } = await authApi.passkeyLoginOptions(null);
        if (!live) return;
        credential = await startAuthentication({ optionsJSON: options, useBrowserAutofill: true });
      } catch {
        // Autofill runs unasked: a failure before anybody picks a passkey
        // (unsupported, API down, prompt replaced) stays silent.
        return;
      }
      try {
        await finishPasskeyLogin(null, credential);
      } catch (err) {
        if (live) setApiError(err instanceof ApiError ? err.message : 'Passkey login failed.');
      }
    })();
    return () => {
      live = false;
      WebAuthnAbortService.cancelCeremony();
    };
    // finishPasskeyLogin only closes over stable values (router, loginWithPasskey).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  async function finishPasskeyLogin(email: string | null, credential: unknown) {
    await loginWithPasskey(email, credential);
    router.replace(safeNextPath(window.location.search));
  }

  function validateEmail() {
    const e: Record<string, string> = {};
    if (!form.email) e.email = 'Required';
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  function validatePassword() {
    const e: Record<string, string> = {};
    if (!form.password) e.password = 'Required';
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleEmailSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validateEmail()) return;
    setLoading(true);
    setApiError('');
    try {
      const options = await authApi.adminLoginOptions(form.email);
      if (!options.can_access_console) {
        throw new ApiError(403, 'This email does not have console access.');
      }
      setLoginOptions(options);
      setForm(f => ({ ...f, email: options.email, password: '' }));
      setStep('auth');
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Could not load login options.');
    } finally {
      setLoading(false);
    }
  }

  async function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validatePassword()) return;
    setLoading(true);
    setApiError('');
    try {
      await login(form.email, form.password);
      router.replace(safeNextPath(window.location.search));
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : 'Login failed.');
    } finally {
      setLoading(false);
    }
  }

  // `email: null` is the first-step, usernameless sign-in; with an email it is
  // scoped to that account's passkeys (the second step).
  async function handlePasskeyLogin(email: string | null) {
    setPasskeyLoading(true);
    setApiError('');
    try {
      const { options } = await authApi.passkeyLoginOptions(email);
      const credential = await startAuthentication({ optionsJSON: options });
      await finishPasskeyLogin(email, credential);
    } catch (err) {
      if (isPasskeyCancel(err)) return;
      setApiError(err instanceof ApiError ? err.message : 'Passkey login failed.');
    } finally {
      setPasskeyLoading(false);
    }
  }

  function goBackToEmail() {
    setStep('email');
    setLoginOptions(null);
    setForm(f => ({ ...f, password: '' }));
    setApiError('');
    setErrors({});
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4 bg-gray-50">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-primary mb-1">MM Admin</h1>
          <p className="text-xs text-gray-400 font-body uppercase tracking-widest">Melting Moments Cakes</p>
        </div>

        <div className="bg-white border border-gray-200 p-6">
          <form onSubmit={step === 'email' ? handleEmailSubmit : handlePasswordSubmit} className="space-y-4">
            {apiError && (
              <div className="bg-red-50 border border-red-200 text-red-600 text-xs px-3 py-2">
                {apiError}
              </div>
            )}
            {step === 'email' ? (
              <>
                <Input
                  label="Email"
                  type="email"
                  value={form.email}
                  onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                  error={errors.email}
                  autoComplete="email webauthn"
                  autoFocus
                />
                <Button type="submit" className="w-full" loading={loading} disabled={passkeyLoading}>
                  Continue
                </Button>
                {passkeysSupported && (
                  <>
                    <div className="flex items-center gap-3">
                      <div className="h-px flex-1 bg-gray-100" />
                      <span className="text-[10px] font-body uppercase tracking-widest text-gray-300">or</span>
                      <div className="h-px flex-1 bg-gray-100" />
                    </div>
                    <Button
                      type="button"
                      variant="secondary"
                      className="w-full"
                      loading={passkeyLoading}
                      disabled={loading}
                      onClick={() => handlePasskeyLogin(null)}
                    >
                      Sign in with a passkey
                    </Button>
                  </>
                )}
              </>
            ) : (
              <>
                <div className="flex items-center justify-between gap-3 border border-gray-100 bg-gray-50 px-3 py-2">
                  <span className="truncate text-xs font-body text-gray-500">{form.email}</span>
                  <button
                    type="button"
                    onClick={goBackToEmail}
                    className="text-xs font-body uppercase tracking-widest text-primary hover:text-primary/70"
                  >
                    Change
                  </button>
                </div>

                {loginOptions?.has_passkey && (
                  <>
                    <Button
                      type="button"
                      variant="secondary"
                      className="w-full"
                      loading={passkeyLoading}
                      onClick={() => handlePasskeyLogin(form.email)}
                    >
                      Use Passkey
                    </Button>
                    <div className="flex items-center gap-3">
                      <div className="h-px flex-1 bg-gray-100" />
                      <span className="text-[10px] font-body uppercase tracking-widest text-gray-300">or</span>
                      <div className="h-px flex-1 bg-gray-100" />
                    </div>
                  </>
                )}

                <Input
                  label="Password"
                  type="password"
                  value={form.password}
                  onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                  error={errors.password}
                  autoComplete="current-password"
                />
                <Button type="submit" className="w-full" loading={loading}>
                  Sign In
                </Button>
              </>
            )}
          </form>
        </div>

        <p className="text-center text-xs text-gray-400 font-body mt-4">
          Admin access only
        </p>
      </div>
    </div>
  );
}
