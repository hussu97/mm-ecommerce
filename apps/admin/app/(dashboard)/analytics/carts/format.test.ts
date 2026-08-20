/**
 * How long a basket has been sitting, in the words somebody says out loud.
 *
 * `formatTimeAgo` in `lib/utils` gives up past an hour and prints a clock time
 * instead, which is right for a driver's position — nothing quoting it is shown
 * once it is that stale — and wrong here, where the interesting rows are
 * precisely the ones measured in days.
 */

import { describe, expect, it } from 'vitest';
import { idleLabel, idleTone } from './format';

describe('idleLabel', () => {
  it('reads in minutes under the hour', () => {
    expect(idleLabel(0)).toBe('just now');
    expect(idleLabel(1)).toBe('1 min');
    expect(idleLabel(59)).toBe('59 min');
  });

  it('reads in hours and minutes under the day', () => {
    expect(idleLabel(60)).toBe('1h');
    expect(idleLabel(200)).toBe('3h 20m');
    expect(idleLabel(1439)).toBe('23h 59m');
  });

  it('reads in days past that', () => {
    expect(idleLabel(1440)).toBe('1 day');
    expect(idleLabel(2880)).toBe('2 days');
  });

  it('says nothing rather than guessing when there is no stamp', () => {
    expect(idleLabel(null)).toBe('—');
  });
});

describe('idleTone', () => {
  it('warms through amber and into red as a basket goes cold', () => {
    expect(idleTone(30)).toContain('gray');
    expect(idleTone(360)).toContain('amber');
    expect(idleTone(1440)).toContain('red');
    expect(idleTone(null)).toContain('gray');
  });
});
