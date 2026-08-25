import { interpolate } from './interpolate';

type TFunction = (key: string, params?: Record<string, string | number>) => string;

/**
 * A translated string, or a hard-coded wording when the key is not seeded yet.
 *
 * Both `createT` (server) and the provider's `t` (client) return the key itself
 * when it is missing, which is how `checkout.estimated_delivery` once shipped to
 * customers verbatim. Strings whose DB rows land in a later deploy than the code
 * that reads them go through here so the gap shows a real word instead.
 *
 * `params` fills `{name}` placeholders in **both** branches — `t` interpolates a
 * found string, and the fallback is interpolated here — so a templated wording
 * like "Get it in {minutes} minutes" reads the same whether or not its row has
 * been seeded yet, instead of leaking a raw `{minutes}` while the deploy catches
 * up.
 */
export function withFallback(
  t: TFunction,
  key: string,
  fallback: string,
  params?: Record<string, string | number>,
): string {
  const value = t(key, params);
  if (value !== key) return value;
  return params ? interpolate(fallback, params) : fallback;
}
