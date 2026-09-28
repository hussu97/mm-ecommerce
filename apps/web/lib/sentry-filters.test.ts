import { describe, expect, it } from 'vitest';
import type { ErrorEvent } from '@sentry/nextjs';
import { isInAppBrowserHydrationError } from './sentry-filters';

const INSTAGRAM =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 448.0.0.0.0';
const FACEBOOK =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 [FBAN/FBIOS;FBAV/500.0.0.0.0]';
const SAFARI =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1';

function errorEvent(value: string): ErrorEvent {
  return { type: undefined, exception: { values: [{ type: 'Error', value }] } };
}

const HYDRATION = errorEvent(
  'Minified React error #418; visit https://react.dev/errors/418?args[]=HTML for the full message',
);

describe('isInAppBrowserHydrationError', () => {
  it('drops a hydration error from an in-app browser', () => {
    expect(isInAppBrowserHydrationError(HYDRATION, INSTAGRAM)).toBe(true);
    expect(isInAppBrowserHydrationError(HYDRATION, FACEBOOK)).toBe(true);
    expect(
      isInAppBrowserHydrationError(
        errorEvent("Hydration failed because the server rendered HTML didn't match the client."),
        INSTAGRAM,
      ),
    ).toBe(true);
  });

  it('keeps a hydration error from a normal browser', () => {
    expect(isInAppBrowserHydrationError(HYDRATION, SAFARI)).toBe(false);
  });

  it('keeps any other error from an in-app browser', () => {
    expect(
      isInAppBrowserHydrationError(errorEvent("Cannot read properties of undefined (reading 'id')"), INSTAGRAM),
    ).toBe(false);
    expect(
      isInAppBrowserHydrationError(errorEvent('Minified React error #310; visit https://react.dev/errors/310'), INSTAGRAM),
    ).toBe(false);
  });
});
