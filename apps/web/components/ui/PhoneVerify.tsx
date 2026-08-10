'use client';

import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { Turnstile, isTurnstileEnabled } from '@/components/ui/Turnstile';
import { analytics, type Surface } from '@/lib/analytics';
import { authApi } from '@/lib/api';
import { getFirebaseAuth, getRecaptchaVerifier, isFirebaseConfigured } from '@/lib/firebase';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { withFallback } from '@/lib/i18n/fallback';
import { Icon } from '@/components/ui/Icon';

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
 *
 * **Every send costs money.** `signInWithPhoneNumber` goes browser→Google, so
 * our server is not in the path and cannot rate limit it; Firebase bills per SMS
 * *sent* — $0.09 to a UAE number — whether or not the code is ever typed back.
 * The guards below are all there is between a customer's index finger and the
 * bill: ask before paying, one send per minute, and a ceiling.
 *
 * They were added while chasing a charge that turned out to be a VM, and they
 * are worth keeping anyway. Measured at the time: three `SendVerificationCode`
 * calls in twelve days, so nothing had actually been overspent — but "Resend
 * code" had no cooldown and no ceiling, and the checkout never asked whether the
 * number was already proved, so the exposure was one impatient customer wide.
 *
 * They are honest-user guards, not a security control. Anything driving Google's
 * REST endpoint with the public API key skips this file entirely — the AE-only
 * SMS region policy, App Check and reCAPTCHA SMS Defense are the parts Google
 * enforces, and they are configured in a console rather than here.
 */

type Step = 'idle' | 'sending' | 'code' | 'verifying' | 'done';

/**
 * The wait between two sends, in seconds.
 *
 * Long enough that an SMS has a fair chance to arrive first — resending because
 * the network was slow buys a second message and no new information.
 */
const RESEND_COOLDOWN_SECONDS = 60;

/**
 * How many messages one mount of this panel may ever buy.
 *
 * A customer who has burned three has a problem no fourth SMS will solve — a
 * wrong number, a dead SIM, a carrier dropping the sender. Past here the panel
 * says so instead of continuing to spend.
 */
const MAX_SENDS = 3;

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

  // When another send becomes allowed, and how many have been bought.
  //
  // A deadline rather than a counter that ticks down. Counting seconds is
  // subject to whatever the browser does to timers in a background tab — and
  // this panel is exactly the one a customer leaves to go and read an SMS. A
  // decremented counter would still owe them 40 seconds when they came back; a
  // deadline has simply passed.
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [sends, setSends] = useState(0);
  const cooldown = Math.max(0, Math.ceil((cooldownUntil - now) / 1000));

  // Twice a second, so the number shown is never a second behind the deadline it
  // is derived from. Runs only while one is pending.
  useEffect(() => {
    if (cooldownUntil <= Date.now()) return;
    const ticker = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(ticker);
  }, [cooldownUntil]);

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
    // Cheap refusals first, before anything is spent. The buttons are disabled
    // in these states too; this is the guard that holds when they are not —
    // a double click landing between renders, or a caller wiring its own button.
    if (step === 'sending' || cooldown > 0 || sends >= MAX_SENDS) return;

    // Our bot check now sits in front of the *paid* action rather than only in
    // front of the ledger write. It stops a script driving this component; it
    // does not stop one driving Google directly, which is App Check's job.
    if (isTurnstileEnabled() && !turnstileToken) {
      setError(
        label('verify.checking', 'Still running the security check — one moment.'),
      );
      return;
    }

    // Six events for one short flow, because it gates a discount and each step
    // fails for its own reason. An SMS that never arrives, a code typed wrong
    // and a Firebase quota are three different problems and the customer sees
    // one panel — this is the only place the difference is visible.
    if (step === 'code') analytics.phoneVerifyResent({ surface });
    else analytics.phoneVerifyStarted({ surface });
    setError(null);
    setStep('sending');

    // A proof belongs to the number, not to the panel it was made in, and it
    // outlives this component by `PHONE_VERIFICATION_TTL_SECONDS`. Asking costs
    // one request; not asking costs an SMS. The account page already checked
    // this while the customer typed — checkout never did, so the customer who
    // verified on Tuesday paid for a second message on Wednesday.
    try {
      const existing = await authApi.phoneVerified(phone);
      if (existing.verified) {
        analytics.phoneVerifySucceeded({ surface });
        setStep('done');
        onVerified(existing.phone);
        return;
      }
    } catch {
      // Fails open: a check we could not make must not block a verification the
      // customer can still complete the expensive way.
    }

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
      // The line above is the one that costs $0.09. Count it here and nowhere
      // else — a send that threw bought nothing and must not spend the ceiling.
      setSends((n) => n + 1);
      setCooldownUntil(Date.now() + RESEND_COOLDOWN_SECONDS * 1000);
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
      // Cool down on failure as well. A send that fails is the one a customer
      // retries hardest, and "too-many-requests" in particular means Firebase is
      // already refusing — hammering it converts a slow minute into a quota ban.
      setCooldownUntil(Date.now() + RESEND_COOLDOWN_SECONDS * 1000);
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
        <Icon name="check_circle" className="text-[18px]" />
        {label('verify.verified', 'Verified')}
      </p>
    );
  }

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {/* Firebase renders its invisible reCAPTCHA into this node. */}
      <div id={recaptchaId} />

      {/* `sending` belongs here once a code is already outstanding: a resend
          would otherwise tear the code field down mid-flight and hand the
          customer back the "Send code" button they just pressed. */}
      {step === 'code' || step === 'verifying' || (step === 'sending' && sends > 0) ? (
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
          {sends >= MAX_SENDS ? (
            // No fourth message. Say why, and point at the thing that is
            // actually wrong — by this point it is the number, not the network.
            <p className="text-xs text-gray-500 self-start">
              {label(
                'verify.no_more_sends',
                'That’s all the codes we can send to this number. Check it’s correct, or contact us.',
              )}
            </p>
          ) : (
            <button
              type="button"
              onClick={send}
              disabled={cooldown > 0 || step === 'sending'}
              className="text-xs text-primary underline self-start disabled:no-underline disabled:text-gray-400"
            >
              {cooldown > 0
                ? `${label('verify.resend_in', 'Resend in')} ${cooldown}s`
                : label('verify.resend', 'Resend code')}
            </button>
          )}
        </>
      ) : (
        <button
          type="button"
          onClick={send}
          disabled={!phone || step === 'sending' || cooldown > 0 || sends >= MAX_SENDS}
          className="bg-primary text-white text-xs uppercase tracking-widest px-4 py-2 self-start disabled:opacity-50"
        >
          {cooldown > 0
            ? `${label('verify.resend_in', 'Resend in')} ${cooldown}s`
            : label('verify.send_code', 'Send code')}
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
