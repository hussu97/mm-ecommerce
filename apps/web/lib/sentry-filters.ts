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
