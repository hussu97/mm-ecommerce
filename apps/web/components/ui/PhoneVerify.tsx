'use client';

import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { Turnstile, isTurnstileEnabled } from '@/components/ui/Turnstile';
import { analytics, type Surface } from '@/lib/analytics';
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
  surface = 'checkout',
}: {
  /** E.164, as `PhoneInput` produces it. */
  phone: string;
  /** Called with the number the *server* confirmed, not the one passed in. */
  onVerified: (verifiedPhone: string) => void;
  className?: string;
  /** Where this panel is embedded, so the funnel can be read per context. */
  surface?: Surface;
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

  // Firebase renders its reCAPTCHA widget *into* a DOM node and refuses to
  // render a second one into the same node — so constructing a fresh verifier
  // per send throws on "Resend code", and on any retry after a failed send. The
  // widget is torn down before a new one is made, and the reference is kept so
  // it can be. This is the bug that makes the resend button useless, which is
  // the button somebody presses precisely when the SMS did not arrive.
  const verifierRef = useRef<{ clear: () => void } | null>(null);
  const recaptchaId = useId().replace(/:/g, '');

  // A component that unmounts mid-flow — the customer collapses the panel, the
  // promo step re-renders — must not leave a live widget behind on the node.
  useEffect(() => () => {
    try {
      verifierRef.current?.clear();
    } catch {
      /* already gone */
    }
  }, []);

  const label = useCallback(
    (key: string, english: string) => withFallback(t, key, english),
    [t],
  );

  // A build without the Firebase vars should look like a site that does not
  // offer verification, not one where the button is broken.
  if (!isFirebaseConfigured()) return null;

  const send = async () => {
    // Six events for one short flow, because it gates a discount and each step
    // fails for its own reason. An SMS that never arrives, a code typed wrong
    // and a Firebase quota are three different problems and the customer sees
    // one panel — this is the only place the difference is visible.
    if (step === 'code') analytics.phoneVerifyResent({ surface });
    else analytics.phoneVerifyStarted({ surface });
    setError(null);
    setStep('sending');
    try {
      const { signInWithPhoneNumber } = await import('firebase/auth');
      const auth = await getFirebaseAuth();
      // Tear the previous widget down first — see `verifierRef`.
      try {
        verifierRef.current?.clear();
      } catch {
        /* nothing rendered yet, or already cleared */
      }
      const verifier = await getRecaptchaVerifier(recaptchaId);
      verifierRef.current = verifier;
      confirmationRef.current = await signInWithPhoneNumber(auth, phone, verifier);
      analytics.phoneVerifySent({ surface });
      setStep('code');
    } catch (err) {
      // Firebase's own messages name quotas and reCAPTCHA and are not for
      // customers. The distinction worth surfacing is "slow down" versus
      // "something went wrong".
      const raw = err instanceof Error ? err.message : '';
      const rateLimited = /too-many|quota/i.test(raw);
      analytics.phoneVerifySendFailed({
        surface,
        reason: rateLimited ? 'rate_limited' : 'unavailable',
      });
      setError(
        rateLimited
          ? label('verify.too_many', 'Too many attempts. Try again in a few minutes.')
          : label('verify.unavailable', 'Verification is unavailable right now.'),
      );
      // A verifier that was rendered before the send failed still occupies the
      // node, and the retry the customer is about to make would collide with
      // it. Clearing here is what makes "try again" mean try again.
      try {
        verifierRef.current?.clear();
      } catch {
        /* already gone */
      }
      verifierRef.current = null;
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
      analytics.phoneVerifySucceeded({ surface });
      setStep('done');
      onVerified(result.phone);
    } catch {
      analytics.phoneVerifyFailed({ surface });
      setError(label('verify.failed', "That code didn't match. Try again."));
      setStep('code');
    }
  };

  if (step === 'done') {
    return (
      <p className={`flex items-center gap-1.5 text-sm text-green-700 ${className}`}>
        <span aria-hidden="true" className="material-icons text-[18px]">
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
