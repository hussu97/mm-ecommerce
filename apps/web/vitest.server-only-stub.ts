/**
 * Vitest stand-in for the `server-only` marker package.
 *
 * The real package throws on import unless the resolver runs under the
 * `react-server` condition — that is its whole job, and Next provides the
 * condition for real Server Component builds. Vitest resolves the default
 * condition, so importing anything that touches `lib/api-server.ts` would
 * throw before the test ran. Aliased here to nothing: the boundary the marker
 * enforces is a bundler concern, and the build (not the unit tests) is where
 * it is verified.
 */
export {};
