/**
 * How a basket's age reads on the Live Baskets screen.
 *
 * Its own module rather than a pair of exports from `page.tsx`: a route file is
 * meant to export its default and Next's own configuration keys, and a helper
 * hanging off one is a helper a framework upgrade is entitled to complain
 * about.
 */

/**
 * How long a basket has been sitting, in the words somebody says out loud.
 *
 * `formatTimeAgo` in `lib/utils` switches to a clock time past an hour, which
 * is right for a driver's position and wrong here: these run to days, and
 * "2 days" is the whole reading.
 */
export function idleLabel(minutes: number | null): string {
  if (minutes === null) return '—';
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours}h ${rest}m` : `${hours}h`;
  }
  const days = Math.floor(minutes / 1440);
  return days === 1 ? '1 day' : `${days} days`;
}

/** Past six hours a basket is cooling; past a day it is cold. */
export function idleTone(minutes: number | null): string {
  if (minutes === null) return 'text-gray-400';
  if (minutes >= 1440) return 'text-red-500';
  if (minutes >= 360) return 'text-amber-600';
  return 'text-gray-600';
}
