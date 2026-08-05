'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Cloudflare Turnstile, on the two forms that make us send mail to an address
 * the person typed: signing up, and asking for a password reset.
 *
 * Invisible to almost everyone — most visitors solve it without seeing
 * anything, which is the reason for choosing it over a puzzle. Only a visitor
 * Cloudflare is unsure about is shown a checkbox, and the widget takes up no
 * room until that happens.
 *
 * With no site key configured the component renders nothing and reports an
 * empty token. The API behaves the same way with no secret: unset means off, at
 * both ends, so a preview deployment or a developer machine is not blocked by a
 * key it was never given.
 */

const SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? '';
const SCRIPT_SRC =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      remove: (id: string) => void;
      reset: (id?: string) => void;
    };
  }
}

let scriptPromise: Promise<void> | null = null;

/** Loaded once per page, however many widgets ask for it. */
function loadScript(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  if (window.turnstile) return Promise.resolve();
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<void>((resolve, reject) => {
    const el = document.createElement('script');
    el.src = SCRIPT_SRC;
    el.async = true;
    el.defer = true;
    el.onload = () => resolve();
    el.onerror = () => {
      scriptPromise = null;
      reject(new Error('turnstile script failed to load'));
    };
    document.head.appendChild(el);
  });
  return scriptPromise;
}

export function isTurnstileEnabled(): boolean {
  return Boolean(SITE_KEY);
}

export function Turnstile({
  onToken,
  theme = 'light',
  className,
}: {
  /** Called with the solution, and with '' whenever it stops being valid. */
  onToken: (token: string) => void;
  theme?: 'light' | 'dark' | 'auto';
  className?: string;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);
  const [failed, setFailed] = useState(false);

  // Kept in a ref so re-rendering the parent — which a controlled form does on
  // every keystroke — does not tear the widget down and make the visitor solve
  // it again. Assigned in an effect rather than during render, which is the
  // rule: a render can be thrown away and restarted, and a ref written during
  // one that never commits leaves the widget calling into a stale closure.
  const emit = useRef(onToken);
  useEffect(() => {
    emit.current = onToken;
  }, [onToken]);

  const mount = useCallback(() => {
    if (!SITE_KEY || !holder.current || widgetId.current) return;
    widgetId.current = window.turnstile!.render(holder.current, {
      sitekey: SITE_KEY,
      theme,
      callback: (token: string) => emit.current(token),
      // A solution is single-use and expires. Clearing the token on both means
      // the form asks again rather than submitting something already spent,
      // which the API would reject as `timeout-or-duplicate`.
      'expired-callback': () => emit.current(''),
      'error-callback': () => {
        emit.current('');
        setFailed(true);
      },
    });
  }, [theme]);

  useEffect(() => {
    if (!SITE_KEY) return;
    let cancelled = false;

    loadScript()
      .then(() => {
        if (!cancelled) mount();
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
      if (widgetId.current && window.turnstile) {
        window.turnstile.remove(widgetId.current);
        widgetId.current = null;
      }
    };
  }, [mount]);

  if (!SITE_KEY) return null;

  return (
    <div className={className}>
      <div ref={holder} />
      {failed && (
        <p className="text-xs text-gray-500 mt-1">
          The security check couldn&rsquo;t load. Check your connection and refresh
          the page.
        </p>
      )}
    </div>
  );
}
