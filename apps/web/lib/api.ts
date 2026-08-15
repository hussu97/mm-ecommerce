/**
 * Compatibility shim over the split API layer. **New code should import the
 * split modules directly** — `lib/api-client.ts` in browser code,
 * `lib/api-server.ts` in Server Components and route handlers.
 *
 * This file used to hold four ways of calling the backend in one module: the
 * wrapped client `request()`, two raw-fetch bypasses of it, and a server-only
 * function whose base URL rules are different enough to have caused a
 * documented build hang. The split gives each side an import-time guard
 * (`client-only` / `server-only`), so picking the wrong one is a build error
 * instead of a production incident.
 *
 * Only the client half can be re-exported from here: the ~40 imports that
 * predate the split are all client-side, and re-exporting `api-server.ts` too
 * would drag its `server-only` marker into every client bundle. Server callers
 * were moved to `lib/api-server.ts` when the split landed.
 */
export * from './api-client';
