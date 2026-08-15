/**
 * Fill `{name}` placeholders in a translated template.
 *
 * One implementation, used by both `createT` on the server and the client
 * provider's `t` — they carried private copies of this loop, which is exactly
 * how the two sides of the same translation drift. Both copies also used
 * single-occurrence `String.replace`, so a template that mentions the same
 * placeholder twice ("{name}, your order for {name} is ready") shipped its
 * second `{name}` verbatim. Split/join replaces every occurrence, and does it
 * without treating the placeholder as a regex.
 */
export function interpolate(
  template: string,
  params?: Record<string, string | number>,
): string {
  if (!params) return template;
  let value = template;
  for (const [k, v] of Object.entries(params)) {
    value = value.split(`{${k}}`).join(String(v));
  }
  return value;
}
