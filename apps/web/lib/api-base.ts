/**
 * The storefront's public API base — a relative path in dev, so the browser
 * goes through the Next rewrite and cookies stay same-origin, and an absolute
 * URL wherever `NEXT_PUBLIC_API_URL` is set (production, previews).
 *
 * Shared by both halves of the split API layer: `api-client.ts` fetches
 * against it directly, `api-server.ts` derives the absolute `RSC_API_BASE`
 * from it. It lives in its own file so the two sides cannot drift on what
 * "the API" means — import one of those modules rather than this one.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api/v1';
