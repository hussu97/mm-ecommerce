/**
 * The analytics beacon, forwarded to Umami Cloud with the visitor still attached.
 *
 * Every event is proxied through this origin rather than posted to
 * `cloud.umami.is` directly, because that hostname sits on the common privacy
 * blocklists and the shop's traffic is overwhelmingly mobile. That part works
 * and is not changing here.
 *
 * What it cost us was geography. A platform rewrite opens its own connection to
 * Umami, so the address Umami sees is the Vercel edge that relayed the event,
 * not the person. Umami Cloud is itself behind Cloudflare, which stamps
 * `cf-connecting-ip` and `cf-ipcountry` onto that connection — and Umami reads
 * the Cloudflare pair ahead of anything a proxy forwards, so simply adding
 * `X-Forwarded-For` would have changed nothing. The dashboard was reporting the
 * relay's location: India and Singapore for a Sharjah bakery, and never the UAE.
 *
 * Two things are sent instead, either of which is enough on its own:
 *
 *   - `payload.ip`. Umami prefers it over every header and, having been given
 *     it, skips header-based lookup entirely — including Cloudflare's. This is
 *     the one that cannot be overwritten upstream.
 *   - `x-umami-client-ip` and the `x-umami-client-{country,region,city}` trio,
 *     which Umami Cloud reads before the Cloudflare headers. Vercel has already
 *     resolved the visitor's location at the edge, so this costs a header copy
 *     and no lookup.
 *
 * Nothing about this is visible to a blocker: same path, same origin, same
 * shape of request as before.
 */

// Runs at the edge nearest the visitor, as the rewrite it replaces did. Events
// fired while the page is being replaced — `begin_checkout`, the hop out to the
// payment gateway — are the ones that cannot afford a trip to a fixed region.
export const runtime = 'edge';

const UMAMI_SEND_URL = 'https://cloud.umami.is/api/send';

/** What the tracker sets and Umami needs to read unchanged. */
const TRACKER_HEADERS = [
  'content-type',
  'user-agent',
  'x-umami-website-id',
  'x-umami-hostname',
  'x-umami-cache',
];

/** Vercel's edge geolocation of the visitor → the headers Umami Cloud reads first. */
const GEO_HEADERS: [from: string, to: string][] = [
  ['x-vercel-ip-country', 'x-umami-client-country'],
  ['x-vercel-ip-country-region', 'x-umami-client-region'],
  ['x-vercel-ip-city', 'x-umami-client-city'],
];

/**
 * Addresses that would place a visitor nowhere.
 *
 * Loopback, link-local and the private ranges — what `x-forwarded-for` carries
 * in dev and behind a local proxy. Passing one on would have Umami look up an
 * address that resolves to no country and drop the location silently, which is
 * harder to notice than sending nothing at all.
 */
const PRIVATE_IP =
  /^(?:::1|::ffff:127\.|127\.|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|f[cd])/i;

function getClientIp(headers: Headers): string | null {
  const ip =
    headers.get('x-forwarded-for')?.split(',')[0].trim() || headers.get('x-real-ip')?.trim();

  return ip && !PRIVATE_IP.test(ip) ? ip : null;
}

/**
 * The visitor's address, written into the event body.
 *
 * A body we cannot parse is forwarded exactly as it arrived. Losing the country
 * on one malformed event is a great deal better than losing the event.
 */
function withClientIp(body: string, ip: string): string {
  try {
    const parsed = JSON.parse(body);
    if (!parsed?.payload || typeof parsed.payload !== 'object') return body;

    parsed.payload.ip = ip;
    return JSON.stringify(parsed);
  } catch {
    return body;
  }
}

export async function POST(request: Request) {
  const headers = new Headers();

  for (const name of TRACKER_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  for (const [from, to] of GEO_HEADERS) {
    const value = request.headers.get(from);
    if (value) headers.set(to, value);
  }

  const ip = getClientIp(request.headers);
  if (ip) headers.set('x-umami-client-ip', ip);

  const body = await request.text();

  try {
    const response = await fetch(UMAMI_SEND_URL, {
      method: 'POST',
      headers,
      body: ip ? withClientIp(body, ip) : body,
    });

    return new Response(await response.text(), {
      status: response.status,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'text/plain',
        'cache-control': 'no-store',
      },
    });
  } catch {
    // Umami unreachable. The tracker ignores the response either way, and an
    // unhandled rejection here would surface as a page error for a beacon the
    // visitor never asked for.
    return new Response(null, { status: 204, headers: { 'cache-control': 'no-store' } });
  }
}
