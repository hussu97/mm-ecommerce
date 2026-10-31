import { timingSafeEqual } from 'node:crypto';
import { revalidateTag } from 'next/cache';
import { NextResponse, type NextRequest } from 'next/server';
import { CACHE_TAGS } from '@/lib/cache-policy';

/**
 * The API's on-demand purge: `POST { "tags": ["catalogue", …] }` with
 * `Authorization: Bearer <STOREFRONT_REVALIDATE_SECRET>`.
 *
 * The API calls this after a committed write to anything the storefront
 * caches, and when the clock alone changes something (a timed stockout or a
 * coupon window) — `apps/api/app/services/storefront_cache.py`. Each tag is
 * revalidated with the `max` profile: the next request for a page built from it
 * is answered from the stale copy while a fresh one is rendered behind it,
 * which is exactly what a TTL expiry does today — only now it happens seconds
 * after the change instead of whenever the minute runs out.
 *
 * Refuses anything it was not built for. No secret configured means the route
 * does not exist (404), so an unconfigured deployment cannot be made to purge
 * by anyone. A wrong secret is a 401; a tag outside `CACHE_TAGS` is a 400,
 * because the only caller sends a fixed set and anything else is a mistake or a
 * probe. `matcher` in `proxy.ts` excludes `/api`, so no locale redirect runs
 * in front of it.
 */
export const dynamic = 'force-dynamic';

const KNOWN_TAGS: ReadonlySet<string> = new Set(Object.values(CACHE_TAGS));

/** At most this many tags per call — the whole set, with room to spare. */
const MAX_TAGS = 16;

function authorised(header: string | null, secret: string): boolean {
  const presented = Buffer.from(header?.startsWith('Bearer ') ? header.slice(7) : '');
  const expected = Buffer.from(secret);
  return presented.length === expected.length && timingSafeEqual(presented, expected);
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const secret = process.env.STOREFRONT_REVALIDATE_SECRET ?? '';
  if (!secret) {
    return NextResponse.json({ error: 'not configured' }, { status: 404 });
  }
  if (!authorised(request.headers.get('authorization'), secret)) {
    return NextResponse.json({ error: 'unauthorised' }, { status: 401 });
  }

  let tags: unknown;
  try {
    tags = ((await request.json()) as { tags?: unknown })?.tags;
  } catch {
    return NextResponse.json({ error: 'body must be JSON' }, { status: 400 });
  }
  if (
    !Array.isArray(tags) ||
    tags.length === 0 ||
    tags.length > MAX_TAGS ||
    !tags.every((tag): tag is string => typeof tag === 'string' && KNOWN_TAGS.has(tag))
  ) {
    return NextResponse.json({ error: 'unknown or missing tags' }, { status: 400 });
  }

  const unique = [...new Set(tags)];
  for (const tag of unique) revalidateTag(tag, 'max');
  return NextResponse.json({ revalidated: unique });
}
