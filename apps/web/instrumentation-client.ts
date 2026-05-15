import * as Sentry from "@sentry/nextjs";

const SENTRY_DSN = process.env.NEXT_PUBLIC_SENTRY_DSN;
const SENTRY_ENVIRONMENT =
  process.env.NEXT_PUBLIC_APP_ENV ?? process.env.NODE_ENV ?? "development";
const isProduction = SENTRY_ENVIRONMENT === "production";

function sampleRate(name: string, fallback: number): number {
  const value = process.env[name];
  if (!value) {
    return fallback;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: SENTRY_ENVIRONMENT,
    sendDefaultPii: false,
    tracesSampleRate: isProduction
      ? sampleRate("NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE", 0.02)
      : 1.0,
    integrations: [
      Sentry.replayIntegration({
        maskAllText: true,
        maskAllInputs: true,
        blockAllMedia: true,
      }),
    ],
    replaysSessionSampleRate: isProduction
      ? sampleRate("NEXT_PUBLIC_SENTRY_REPLAYS_SESSION_SAMPLE_RATE", 0)
      : 0,
    replaysOnErrorSampleRate: 1.0,
  });
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
