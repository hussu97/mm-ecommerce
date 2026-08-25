import { withFallback } from '@/lib/i18n/fallback';
import type { DeliveryArea, DeliverySpeed } from './types';

type TFunction = (key: string, params?: Record<string, string | number>) => string;

/**
 * The one place the three delivery promises are worded.
 *
 * A product card, the PDP badge and the home marquee all answer "how fast does
 * this reach me", and they used to answer it from two copied maps that drifted:
 * the card said "Get it in 60 minutes" from a hardcoded hour while the checkout,
 * reading the courier's real `unbatched_promise_minutes`, said 90. One customer,
 * one pin, two numbers. This resolves the label from the same `express_minutes`
 * the API now quotes, so every surface says what the checkout will.
 */
const SPEED_KEY: Record<DeliverySpeed, string> = {
  express: 'usp.speed_express',
  same_day: 'usp.speed_same_day',
  next_day: 'usp.speed_next_day',
};

/** The wording when the row is not seeded yet. `express` is a template because
 *  its minutes are a per-zone figure, not a brand constant. */
const SPEED_FALLBACK: Record<DeliverySpeed, string> = {
  express: 'Get it in {minutes} minutes',
  same_day: 'Get it today',
  next_day: 'Get it tomorrow',
};

/**
 * The hour an express badge falls back to when the API names no figure — an
 * express zone whose courier promises a day, or a response from before the
 * field existed. The historical wording, so nothing regresses to worse than it
 * said before.
 */
export const DEFAULT_EXPRESS_MINUTES = 60;

/** The delivery promise for this area, translated, with `express` carrying the
 *  courier's real minutes rather than a fixed hour. */
export function speedLabel(
  t: TFunction,
  area: Pick<DeliveryArea, 'speed' | 'express_minutes'>,
): string {
  const speed: DeliverySpeed = SPEED_KEY[area.speed] ? area.speed : 'next_day';
  const params =
    speed === 'express'
      ? { minutes: area.express_minutes ?? DEFAULT_EXPRESS_MINUTES }
      : undefined;
  return withFallback(t, SPEED_KEY[speed], SPEED_FALLBACK[speed], params);
}
