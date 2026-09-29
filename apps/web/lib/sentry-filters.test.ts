import { describe, expect, it } from 'vitest';
import type { ErrorEvent } from '@sentry/nextjs';
import { isInAppBrowserHydrationError, isInjectedScriptError } from './sentry-filters';

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

function eventWithFrames(value: string, filenames: string[]): ErrorEvent {
  return {
    type: undefined,
    exception: {
      values: [{ type: 'Error', value, stacktrace: { frames: filenames.map((filename) => ({ filename })) } }],
    },
  };
}

const INSTAGRAM_LOGGER = 'app://navigation_performance_logger_android';

describe('isInjectedScriptError', () => {
  it('drops the dead-bridge error from Instagram on Android', () => {
    expect(
      isInjectedScriptError(
        eventWithFrames('Error invoking postMessage: Java object is gone', [
          INSTAGRAM_LOGGER,
          INSTAGRAM_LOGGER,
          INSTAGRAM_LOGGER,
        ]),
      ),
    ).toBe(true);
    expect(isInjectedScriptError(errorEvent('Error invoking postMessage: Java object is gone'))).toBe(true);
  });

  it('drops any error thrown wholly inside a host-injected script', () => {
    expect(isInjectedScriptError(eventWithFrames("Cannot read properties of null (reading 'x')", [INSTAGRAM_LOGGER]))).toBe(
      true,
    );
  });

  it('keeps an error that passes through our own bundle', () => {
    const ours = 'app:///_next/static/chunks/app/checkout-1a2b3c.js';
    expect(isInjectedScriptError(eventWithFrames("Cannot read properties of null (reading 'x')", [ours]))).toBe(false);
    expect(
      isInjectedScriptError(eventWithFrames("Cannot read properties of null (reading 'x')", [INSTAGRAM_LOGGER, ours])),
    ).toBe(false);
    expect(
      isInjectedScriptError(
        eventWithFrames('boom', ['https://meltingmomentscakes.com/_next/static/chunks/main.js']),
      ),
    ).toBe(false);
  });

  it('keeps an error with no stack', () => {
    expect(isInjectedScriptError(errorEvent("Cannot read properties of undefined (reading 'id')"))).toBe(false);
  });
});
