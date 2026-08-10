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

/**
 * How long a send will wait for Cloudflare before calling it broken.
 *
 * Long enough to cover a slow connection solving an invisible challenge, short
 * enough that a customer is not left watching a pending button on a page where
 * the check will never complete because something is blocking it.
 */
const TURNSTILE_WAIT_MS = 15_000;

export function PhoneVerify({
  phone,
  onVerified,
  className = '',
  surface = 'checkout',
  onCodePending,
}: {
  /** E.164, as `PhoneInput` produces it. */
  phone: string;
  /** Called with the number the *server* confirmed, not the one passed in. */
  onVerified: (verifiedPhone: string) => void;
  className?: string;
  /** Where this panel is embedded, so the funnel can be read per context. */
  surface?: Surface;
  /**
   * Whether a code is currently outstanding against `phone`.
   *
   * The number field belongs to the form around this panel, not to this panel,
   * and while a code is in flight it must not be editable — a code sent to one
   * number and confirmed against a field showing another is a customer being
   * told their verification failed for no reason they can see. The caller locks
   * its own input on this, and "Change number" below is the way back out.
   *
   * Must be referentially stable — a bare `setState` is ideal.
   */
  onCodePending?: (pending: boolean) => void;
}) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>('idle');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);

  /**
   * Cloudflare's answer, in a ref rather than state.
   *
   * `send` needs to *wait* on this, and awaiting a value that only arrives via
   * a re-render means either an extra effect or a stale closure. A ref is read
   * at the moment it is needed, which is exactly the semantics wanted here.
   * Nothing renders from it, so nothing is lost by it not being state.
   */
  const turnstileRef = useRef<string | null>(null);
  const handleTurnstile = useCallback((token: string) => {
    // Turnstile emits '' when a solution expires as well as on failure, and an
    // expired token is worse than none: the API rejects it as
    // `timeout-or-duplicate` after the SMS has already been paid for.
    turnstileRef.current = token || null;
  }, []);

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

  /**
   * The number the outstanding code was actually sent to.
   *
   * Firebase's confirmation object is bound to one number, so if `phone` moves
   * underneath us the code on screen belongs to a number the customer is no
   * longer looking at. Locking the field is the first defence; this is the one
   * that holds when a caller changes the value some other way.
   */
  const [sentTo, setSentTo] = useState<string | null>(null);

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

  /**
   * Whether a code is outstanding — the state in which the number is fixed.
   *
   * `sending` counts only once one has already been bought, so pressing the
   * first "Send code" does not lock the field before anything has been sent.
   */
  const codePending =
    step === 'code' || step === 'verifying' || (step === 'sending' && sends > 0);

  useEffect(() => {
    onCodePending?.(codePending);
  }, [codePending, onCodePending]);

  // Tell the caller the number is free again when this panel goes away
  // mid-flow, or the form is left with an input nothing will ever unlock.
  useEffect(() => () => onCodePending?.(false), [onCodePending]);

  /**
   * Abandon the outstanding code and return to the start.
   *
   * The spend guards deliberately survive: `sends` and the cooldown are about
   * how many messages this panel may buy, and a mistyped digit does not make
   * the next SMS cheaper. Retyping the number is not a way to earn three more.
   *
   * Cloudflare's solution survives too. It attests to a browser, not to a phone
   * number, and it is still the same browser — making somebody re-solve a bot
   * check for correcting a typo is a penalty for our benefit, not theirs.
   */
  const resetFlow = useCallback(() => {
    confirmationRef.current = null;
    try {
      verifierRef.current?.clear();
    } catch {
      /* already gone */
    }
    verifierRef.current = null;
    setSentTo(null);
    setCode('');
    setError(null);
    setStep('idle');
  }, []);

  // The number moved without going through the button — a caller resetting the
  // form, a saved address being picked. The code on screen belongs to the old
  // one and confirming it would verify a number the customer is no longer
  // looking at.
  useEffect(() => {
    if (sentTo !== null && phone !== sentTo) resetFlow();
  }, [phone, sentTo, resetFlow]);

  // A build without the Firebase vars should look like a site that does not
  // offer verification, not one where the button is broken.
  if (!isFirebaseConfigured()) return null;

  /**
   * Cloudflare's solution, once it arrives, or `null` if it never does.
   *
   * Polled rather than awaited on a promise because the token can also be
   * *replaced* — Turnstile re-issues on expiry — and the useful question is
   * "is there a valid one right now", asked repeatedly, not "tell me once".
   */
  const waitForTurnstile = async (): Promise<string | null> => {
    const deadline = Date.now() + TURNSTILE_WAIT_MS;
    while (Date.now() < deadline) {
      if (turnstileRef.current) return turnstileRef.current;
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
    return null;
  };

  const send = async () => {
    // Cheap refusals first, before anything is spent. The buttons are disabled
    // in these states too; this is the guard that holds when they are not —
    // a double click landing between renders, or a caller wiring its own button.
    if (step === 'sending' || cooldown > 0 || sends >= MAX_SENDS) return;

    // Six events for one short flow, because it gates a discount and each step
    // fails for its own reason. An SMS that never arrives, a code typed wrong
    // and a Firebase quota are three different problems and the customer sees
    // one panel — this is the only place the difference is visible.
    if (step === 'code') analytics.phoneVerifyResent({ surface });
    else analytics.phoneVerifyStarted({ surface });
    setError(null);
    // Set before anything is awaited, so the button says "Sending…" from the
    // instant it is pressed rather than after the first round trip.
    setStep('sending');

    // Our bot check sits in front of the *paid* action rather than only in
    // front of the ledger write. It stops a script driving this component; it
    // does not stop one driving Google directly, which is App Check's job.
    //
    // Cloudflare normally answers before anyone can reach this button, but on a
    // slow connection it is a race — and losing that race is not the customer's
    // problem to read about. This used to refuse the click and print "Still
    // running the security check" in red, which told them about a mechanism
    // they never asked for, in the styling of an error, for something that was
    // not wrong and needed nothing from them. Now it simply waits, behind the
    // pending button they are already looking at.
    if (isTurnstileEnabled() && !turnstileRef.current) {
      const solved = await waitForTurnstile();
      if (!solved) {
        // Genuinely broken now — blocked, offline, or Cloudflare down. This one
        // is worth saying, and it is said the way every other failure here is.
        analytics.phoneVerifySendFailed({ surface, reason: 'unavailable' });
        setError(label('verify.unavailable', 'Verification is unavailable right now.'));
        setStep('idle');
        return;
      }
    }

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
      setSentTo(phone);
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
      const result = await authApi.verifyPhone(idToken, turnstileRef.current);
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
          <div className="flex items-center gap-3">
            {sends >= MAX_SENDS ? (
              // No fourth message. Say why, and point at the thing that is
              // actually wrong — by this point it is the number, not the network.
              <p className="text-xs text-gray-500">
                {label(
                  'verify.no_more_sends',
                  'That’s all the codes we can send to this number.',
                )}
              </p>
            ) : (
              <button
                type="button"
                onClick={send}
                disabled={cooldown > 0 || step === 'sending'}
                className="text-xs text-primary underline disabled:no-underline disabled:text-gray-400"
              >
                {step === 'sending'
                  ? label('verify.sending', 'Sending…')
                  : cooldown > 0
                    ? `${label('verify.resend_in', 'Resend in')} ${cooldown}s`
                    : label('verify.resend', 'Resend code')}
              </button>
            )}

            {/* The way out of a locked field. Always available — this is the
                button somebody reaches for the moment they notice the digit
                they got wrong, which is usually while the first code is still
                in flight. */}
            <button
              type="button"
              onClick={resetFlow}
              className="text-xs text-gray-500 underline"
            >
              {label('verify.change_number', 'Change number')}
            </button>
          </div>
        </>
      ) : (
        <button
          type="button"
          onClick={send}
          disabled={!phone || step === 'sending' || cooldown > 0 || sends >= MAX_SENDS}
          className="bg-primary text-white text-xs uppercase tracking-widest px-4 py-2 self-start disabled:opacity-50"
        >
          {/* "Sending…" covers the Cloudflare wait as well as the send itself.
              Both are the same answer to the only question being asked, which is
              whether the button did anything. */}
          {step === 'sending'
            ? label('verify.sending', 'Sending…')
            : cooldown > 0
              ? `${label('verify.resend_in', 'Resend in')} ${cooldown}s`
              : label('verify.send_code', 'Send code')}
        </button>
      )}

      {isTurnstileEnabled() && <Turnstile onToken={handleTurnstile} />}

      {error && (
        <p role="alert" className="text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
