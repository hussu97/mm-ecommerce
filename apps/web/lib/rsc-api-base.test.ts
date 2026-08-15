import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { RSC_API_BASE } from './api-server';

/**
 * A relative `API_BASE` in server-side code does not throw — in a static
 * prerender the fetch never settles, so the `try`/`catch` fallback never runs
 * and `next build` dies on a 60s worker timeout with no useful error. It only
 * reproduces where `NEXT_PUBLIC_API_URL` is relative, which is every local
 * checkout and no deployment, so CI cannot be relied on to catch it.
 */
describe('RSC_API_BASE', () => {
  it('is absolute even when the public base is a relative dev path', () => {
    expect(RSC_API_BASE).toMatch(/^https?:\/\//);
  });
});

const WEB_ROOT = join(__dirname, '..');

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : sourceFiles(path);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

/** `'use client'` files run in the browser, where the relative base is correct. */
function isClientFile(source: string): boolean {
  return /^\s*(['"])use client\1/.test(source);
}

/**
 * `lib/api-client.ts` is the browser half of the API layer, so it uses the
 * relative base on purpose. It is the one file allowed to — and its
 * `client-only` marker means the server can never import it by accident.
 */
const ALLOWED = new Set(['lib/api-client.ts']);

describe('server-side fetches', () => {
  it('never build a URL from the relative API_BASE', () => {
    const offenders = [join(WEB_ROOT, 'app'), join(WEB_ROOT, 'lib')]
      .flatMap(sourceFiles)
      .filter(path => {
        const source = readFileSync(path, 'utf8');
        if (isClientFile(source)) return false;
        // `${API_BASE}` in a template literal is the shape that builds a URL.
        return /\$\{API_BASE\}/.test(source);
      })
      .map(path => path.slice(WEB_ROOT.length + 1))
      .filter(path => !ALLOWED.has(path));

    expect(offenders).toEqual([]);
  });
});
