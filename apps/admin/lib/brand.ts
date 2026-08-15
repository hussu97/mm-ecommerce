/**
 * The brand palette, for the places that cannot read CSS.
 *
 * Charts (recharts) take colours as string props, so the Tailwind tokens
 * never reach them. These mirror the `@theme` block in `app/globals.css`
 * (`--color-primary` / `--color-secondary` / `--color-tertiary`) — change
 * them together or the dashboard drifts off-brand.
 */
export const BRAND = {
  primary: '#8a5a64',
  secondary: '#d6acab',
  tertiary: '#dfbdc1',
} as const;
