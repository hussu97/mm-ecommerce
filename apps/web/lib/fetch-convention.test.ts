/**
 * Convention 9, as far as it is currently true.
 *
 * The rule says the browser goes through `lib/api-client.ts` and RSC through
 * `lib/api-server.ts`, never a raw `fetch` to the API. The browser half holds
 * and is asserted here. The server half does not, and pretending otherwise
 * with a test that has to be skipped would be worse than saying so:
 *
 * `api-server.ts` exports two bindings — a CMS page and pickup points. There
 * is no `productsApi`, no `categoriesApi`, no `blogApi`, no `i18nApi` on the
 * server side, so every RSC page that needs a product has nothing to call and
 * reaches for `RSC_API_BASE` directly. The rule is unfollowable there as
 * written, and the fix is to grow `api-server.ts`, not to scold the callers.
 *
 * So: the client half is enforced, and the server half is an explicit list
 * that may only shrink. Each file removed from it is one fewer place the ISR
 * error semantics documented in `fetch-json.ts` can be got wrong by hand.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const WEB = join(__dirname, '..');

/** Files that own a fetch, or are not talking to our API at all. */
const SANCTIONED = new Set([
  join('lib', 'api-client.ts'),   // the browser client itself
  join('lib', 'api-server.ts'),   // the RSC client itself
  join('lib', 'fetch-json.ts'),   // the shared RSC fetch, re-exported by api-server
  join('proxy.ts'),               // edge middleware: api-server carries `server-only`
  join('app', 'vague', 'api', 'send', 'route.ts'), // posts to Umami, not to us
]);

/**
 * Server-rendered routes still calling the API directly, because
 * `api-server.ts` has no binding for what they need.
 *
 * **This list may only shrink.** Adding to it means adding a fourth way for
 * the storefront to reach its own API.
 */
const AWAITING_API_SERVER = new Set([
  join('app', 'sitemap.ts'),
  join('app', 'image-sitemap.xml', 'route.ts'),
  join('app', 'llms.txt', 'route.ts'),
  join('app', 'llms-full.txt', 'route.ts'),
  join('app', '[locale]', 'blog', 'page.tsx'),
  join('app', '[locale]', 'blog', '[slug]', 'page.tsx'),
  join('app', '[locale]', 'search', 'page.tsx'),
  join('app', '[locale]', '[category]', '[product]', 'page.tsx'),
  join('lib', 'catalogue.ts'),
  join('lib', 'i18n', 'server.ts'),
]);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (['node_modules', '.next', 'e2e', 'scripts', 'public'].includes(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const FETCHES = /(^|[^A-Za-z0-9_.])fetch\s*\(/m;

const offenders = walk(WEB)
  .map((f) => ({ rel: f.slice(WEB.length + 1), body: readFileSync(f, 'utf8') }))
  .filter(({ body }) => FETCHES.test(body))
  .filter(({ rel }) => !SANCTIONED.has(rel));

describe('convention 9 — one request path per side', () => {
  it('no client component calls fetch directly', () => {
    const clientSide = offenders
      .filter(({ body }) => /^['"]use client['"]/m.test(body))
      .map(({ rel }) => rel)
      .sort();

    expect(
      clientSide,
      "use lib/api-client.ts — a bare fetch skips the 401 refresh and the " +
        "`api_error` analytics hook, so its failures are invisible in Umami",
    ).toEqual([]);
  });

  it('the server-side backlog only shrinks', () => {
    const serverSide = offenders
      .filter(({ body }) => !/^['"]use client['"]/m.test(body))
      .map(({ rel }) => rel)
      .sort();
    const unexpected = serverSide.filter((f) => !AWAITING_API_SERVER.has(f));

    expect(
      unexpected,
      'a new raw fetch on the server side. Add a binding to lib/api-server.ts ' +
        'and call that, rather than adding a fourth way to reach the API',
    ).toEqual([]);
  });

  it('the backlog list has no stale entries', () => {
    const serverSide = new Set(offenders.map(({ rel }) => rel));
    const fixed = [...AWAITING_API_SERVER].filter((f) => !serverSide.has(f)).sort();

    expect(
      fixed,
      'these no longer raw-fetch — remove them from AWAITING_API_SERVER so the ' +
        'list keeps meaning something',
    ).toEqual([]);
  });
});
