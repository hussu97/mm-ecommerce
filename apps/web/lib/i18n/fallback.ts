type TFunction = (key: string, params?: Record<string, string | number>) => string;

/**
 * A translated string, or a hard-coded wording when the key is not seeded yet.
 *
 * Both `createT` (server) and the provider's `t` (client) return the key itself
 * when it is missing, which is how `checkout.estimated_delivery` once shipped to
 * customers verbatim. Strings whose DB rows land in a later deploy than the code
 * that reads them go through here so the gap shows a real word instead.
 */
export function withFallback(t: TFunction, key: string, fallback: string): string {
  const value = t(key);
  return value === key ? fallback : value;
}
