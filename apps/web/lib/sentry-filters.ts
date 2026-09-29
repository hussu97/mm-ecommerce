import type { ErrorEvent } from '@sentry/nextjs';

/**
 * Social apps' in-app browsers (Instagram, Facebook, TikTok, Snapchat) inject
 * their own scripts and nodes into the page before React hydrates, so the DOM
 * React finds is not the one the server sent. The resulting hydration error is
 * theirs, not ours: nothing in our render differs, React recovers by
 * client-rendering, and the visitor sees a working page. Reported, it is an
 * unfixable issue that buries real mismatches from Safari and Chrome.
 */
const IN_APP_BROWSER = /Instagram|FBAN\/|FBAV\/|FB_IAB|musical_ly|BytedanceWebview|TikTok|Snapchat/;

// The same match the Replay SDK uses to tag a hydration error: the minified
// production codes (react.dev/errors/418 etc.) and the development wording.
const HYDRATION_ERROR =
  /(reactjs\.org\/docs\/error-decoder\.html\?invariant=|react\.dev\/errors\/)(418|419|422|423|425)|does not match server-rendered HTML|Hydration failed because/i;

export function isInAppBrowserHydrationError(event: ErrorEvent, userAgent: string): boolean {
  if (!IN_APP_BROWSER.test(userAgent)) return false;
  const message = event.exception?.values?.[0]?.value ?? event.message ?? '';
  return HYDRATION_ERROR.test(message);
}

/**
 * Instagram's Android WebView evaluates its own telemetry scripts in the page
 * (`app://navigation_performance_logger_android`) and talks to the app through
 * a `@JavascriptInterface` bridge. When the app tears the bridge down first —
 * the visitor closes the in-app browser mid-load — the next call throws "Error
 * invoking postMessage: Java object is gone" from inside that script, which our
 * global onerror handler then reports. None of it is our code or our bug.
 *
 * Our own frames are https:// URLs, or `app:///_next/...` once Sentry rewrites
 * them (three slashes), so an `app://<host>` frame is a script the host
 * injected. The error is dropped only when every frame is one of those, so an
 * error that passes through our bundle is still reported.
 */
const INJECTED_SCRIPT_FRAME = /^app:\/\/(?!\/)/;

// Android WebView's message for a call into a removed JavascriptInterface; the
// site never registers or calls one, so it can only come from the host app.
const DEAD_NATIVE_BRIDGE = /Java object is gone/;

export function isInjectedScriptError(event: ErrorEvent): boolean {
  const exception = event.exception?.values?.[0];
  if (DEAD_NATIVE_BRIDGE.test(exception?.value ?? event.message ?? '')) return true;
  const frames = exception?.stacktrace?.frames ?? [];
  return frames.length > 0 && frames.every((frame) => INJECTED_SCRIPT_FRAME.test(frame.filename ?? ''));
}
