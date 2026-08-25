import { describe, it, expect } from 'vitest';
import { speedLabel, DEFAULT_EXPRESS_MINUTES } from './speed';
import type { DeliveryArea } from './types';

// A `t` that interpolates from a catalogue, exactly as the real provider does —
// so the test exercises the placeholder path rather than a stub that ignores it.
const CATALOGUE: Record<string, string> = {
  'usp.speed_express': 'Get it in {minutes} minutes',
  'usp.speed_same_day': 'Get it today',
  'usp.speed_next_day': 'Get it tomorrow',
};
const t = (key: string, params?: Record<string, string | number>) => {
  const template = CATALOGUE[key];
  if (template === undefined) return key;
  if (!params) return template;
  let value = template;
  for (const [k, v] of Object.entries(params)) value = value.split(`{${k}}`).join(String(v));
  return value;
};

const area = (speed: DeliveryArea['speed'], express_minutes?: number | null): DeliveryArea => ({
  serviceable: true,
  zone_name: 'Sharjah',
  delivery_fee: 0,
  free_threshold: 0,
  free_delivery_available: true,
  speed,
  express_minutes,
  branch_id: null,
});

describe('speedLabel', () => {
  it('says the courier’s real minutes for express, not a fixed hour', () => {
    // The bug this change fixes: a Sharjah polygon re-timed to 90 minutes had
    // the card still saying 60. The label must follow the configured figure.
    expect(speedLabel(t, area('express', 90))).toBe('Get it in 90 minutes');
  });

  it('falls back to the historical hour when the API names no minutes', () => {
    expect(speedLabel(t, area('express', null))).toBe(
      `Get it in ${DEFAULT_EXPRESS_MINUTES} minutes`,
    );
    expect(speedLabel(t, area('express', undefined))).toBe(
      `Get it in ${DEFAULT_EXPRESS_MINUTES} minutes`,
    );
  });

  it('leaves same-day and next-day as plain promises', () => {
    expect(speedLabel(t, area('same_day'))).toBe('Get it today');
    expect(speedLabel(t, area('next_day'))).toBe('Get it tomorrow');
  });

  it('interpolates the fallback wording when the row is not seeded yet', () => {
    const bare = (key: string) => key; // every key missing
    expect(speedLabel(bare, area('express', 90))).toBe('Get it in 90 minutes');
  });
});
