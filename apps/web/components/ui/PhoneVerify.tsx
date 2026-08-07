'use client';

import { useCallback, useId, useRef, useState } from 'react';

import { Turnstile, isTurnstileEnabled } from '@/components/ui/Turnstile';
import { authApi } from '@/lib/api';
import { getFirebaseAuth, getRecaptchaVerifier, isFirebaseConfigured } from '@/lib/firebase';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { withFallback } from '@/lib/i18n/fallback';

/**
 * Prove a phone number, in two steps, without leaving the page.
 *
 * Firebase sends and checks the code; the server verifies the receipt and
 * writes it down. Nothing here is trusted — a customer could return any string
 * they liked from this component and the server would refuse it, because what
 * it checks is a signature and an audience, not our word for it.
 *
 * Two bot checks run in this flow and only one is ours. Firebase's web SDK will
 * not send an SMS without an `AppVerifier`, so its reCAPTCHA is unavoidable;
 * ours is invisible and guards the endpoint that records the result. Both are
 * configured to be silent, so the customer normally sees neither.
 */

type Step = 'idle' | 'sending' | 'code' | 'verifying' | 'done';

export function PhoneVerify({
  phone,
  onVerified,
  className = '',
}: {
  /** E.164, as `PhoneInput` produces it. */
  phone: string;
  /** Called with the number the *server* confirmed, not the one passed in. */
  onVerified: (verifiedPhone: string) => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>('idle');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);

  // Firebase hands back a confirmation object, not a token — the code is
  // checked against it, so it has to survive between the two steps.
  const confirmationRef = useRef<{ confirm: (code: string) => Promise<unknown> } | null>(
    null,
  );
  const recaptchaId = useId().replace(/:/g, '');

  const label = useCallback(
    (key: string, english: string) => withFallback(t, key, english),
    [t],
  );

  // A build without the Firebase vars should look like a site that does not
  // offer verification, not one where the button is broken.
  if (!isFirebaseConfigured()) return null;

  const send = async () => {
    setError(null);
    setStep('sending');
    try {
      const { signInWithPhoneNumber } = await import('firebase/auth');
      const auth = await getFirebaseAuth();
      const verifier = await getRecaptchaVerifier(recaptchaId);
      confirmationRef.current = await signInWithPhoneNumber(auth, phone, verifier);
      setStep('code');
    } catch (err) {
      // Firebase's own messages name quotas and reCAPTCHA and are not for
      // customers. The distinction worth surfacing is "slow down" versus
      // "something went wrong".
      const raw = err instanceof Error ? err.message : '';
      setError(
        /too-many|quota/i.test(raw)
          ? label('verify.too_many', 'Too many attempts. Try again in a few minutes.')
          : label('verify.unavailable', 'Verification is unavailable right now.'),
      );
      setStep('idle');
    }
  };

  const confirm = async () => {
    if (!confirmationRef.current) return;
    setError(null);
    setStep('verifying');
    try {
      const credential = (await confirmationRef.current.confirm(code)) as {
        user: { getIdToken: () => Promise<string> };
      };
      const idToken = await credential.user.getIdToken();
      // The server is the only thing whose answer counts. It re-verifies the
      // signature and the audience, and returns the number *it* read out of the
      // token — which is what we hand back, rather than what was typed.
      const result = await authApi.verifyPhone(idToken, turnstileToken);
      setStep('done');
      onVerified(result.phone);
    } catch {
      setError(label('verify.failed', "That code didn't match. Try again."));
      setStep('code');
    }
  };

  if (step === 'done') {
    return (
      <p className={`flex items-center gap-1.5 text-sm text-green-700 ${className}`}>
        <span aria-hidden="true" className="material-symbols-outlined text-[18px]">
          check_circle
        </span>
        {label('verify.verified', 'Verified')}
      </p>
    );
  }

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {/* Firebase renders its invisible reCAPTCHA into this node. */}
      <div id={recaptchaId} />

      {step === 'code' || step === 'verifying' ? (
        <>
          <label htmlFor={`${recaptchaId}-code`} className="text-xs text-gray-600">
            {label('verify.code_label', '6-digit code')}
          </label>
          <div className="flex items-center gap-2">
            <input
              id={`${recaptchaId}-code`}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              className="w-32 border border-gray-300 px-3 py-2 text-sm tracking-[0.3em] text-center"
            />
            <button
              type="button"
              onClick={confirm}
              disabled={code.length !== 6 || step === 'verifying'}
              className="bg-primary text-white text-xs uppercase tracking-widest px-4 py-2 disabled:opacity-50"
            >
              {label('verify.confirm', 'Confirm')}
            </button>
          </div>
          <button
            type="button"
            onClick={send}
            className="text-xs text-primary underline self-start"
          >
            {label('verify.resend', 'Resend code')}
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={send}
          disabled={!phone || step === 'sending'}
          className="bg-primary text-white text-xs uppercase tracking-widest px-4 py-2 self-start disabled:opacity-50"
        >
          {label('verify.send_code', 'Send code')}
        </button>
      )}

      {isTurnstileEnabled() && <Turnstile onToken={setTurnstileToken} />}

      {error && (
        <p role="alert" className="text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
