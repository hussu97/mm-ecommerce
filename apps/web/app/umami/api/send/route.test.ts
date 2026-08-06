import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { POST } from './route';

/**
 * The one thing this proxy exists to carry, beyond the event itself, is who
 * sent it. Umami Cloud sits behind Cloudflare, which stamps its own
 * `cf-connecting-ip`/`cf-ipcountry` onto our connection and wins over anything
 * forwarded — so the assertions below are about the two channels that outrank
 * it, and about never dropping an event to save a country.
 */

const UMAMI = 'https://cloud.umami.is/api/send';

const EVENT = {
  type: 'event',
  payload: { website: 'w-1', hostname: 'meltingmomentscakes.com', url: '/ar', name: 'add_to_cart' },
};

function request(headers: Record<string, string> = {}, body: unknown = EVENT) {
  return new Request('https://meltingmomentscakes.com/umami/api/send', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

/** A visitor in Dubai, as Vercel's edge describes them. */
const VISITOR = {
  'x-forwarded-for': '94.200.1.1',
  'x-vercel-ip-country': 'AE',
  'x-vercel-ip-country-region': 'DU',
  'x-vercel-ip-city': 'Dubai',
  'user-agent': 'Mozilla/5.0 (iPhone)',
  'x-umami-website-id': 'w-1',
  'x-umami-cache': 'cached-token',
};

let fetchMock: ReturnType<typeof vi.fn>;

function sent() {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  const raw = init.body as string;

  return {
    url,
    raw,
    headers: new Headers(init.headers),
    get body() {
      return JSON.parse(raw);
    },
  };
}

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(
    new Response('{"cache":"next-token"}', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the visitor reaches Umami', () => {
  it("puts the visitor's address in the body, where Cloudflare cannot overwrite it", async () => {
    await POST(request(VISITOR));

    expect(sent().url).toBe(UMAMI);
    expect(sent().body.payload.ip).toBe('94.200.1.1');
  });

  it('leaves the rest of the event exactly as the tracker built it', async () => {
    await POST(request(VISITOR));

    expect(sent().body).toEqual({
      type: 'event',
      payload: { ...EVENT.payload, ip: '94.200.1.1' },
    });
  });

  it("hands over Vercel's reading of where the visitor is", async () => {
    const { headers } = (await POST(request(VISITOR)), sent());

    expect(headers.get('x-umami-client-ip')).toBe('94.200.1.1');
    expect(headers.get('x-umami-client-country')).toBe('AE');
    expect(headers.get('x-umami-client-region')).toBe('DU');
    expect(headers.get('x-umami-client-city')).toBe('Dubai');
  });

  it('takes the visitor, not the proxies behind them, from x-forwarded-for', async () => {
    await POST(request({ ...VISITOR, 'x-forwarded-for': '94.200.1.1, 10.0.0.7, 172.16.4.2' }));

    expect(sent().body.payload.ip).toBe('94.200.1.1');
  });

  it('forwards the headers the tracker set, so the device is still identified', async () => {
    const { headers } = (await POST(request(VISITOR)), sent());

    expect(headers.get('user-agent')).toBe('Mozilla/5.0 (iPhone)');
    expect(headers.get('x-umami-website-id')).toBe('w-1');
    expect(headers.get('x-umami-cache')).toBe('cached-token');
    expect(headers.get('content-type')).toBe('application/json');
  });

  it("returns Umami's own answer, which is where the tracker reads its cache token", async () => {
    const response = await POST(request(VISITOR));

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ cache: 'next-token' });
  });
});

describe('no address is better than a meaningless one', () => {
  it('sends no location for a private address', async () => {
    for (const ip of ['127.0.0.1', '::1', '10.0.0.4', '192.168.1.9', '172.20.0.1', 'fd00::1']) {
      fetchMock.mockClear();
      await POST(request({ ...VISITOR, 'x-forwarded-for': ip }));

      expect(sent().body.payload, ip).not.toHaveProperty('ip');
      expect(sent().headers.get('x-umami-client-ip'), ip).toBeNull();
    }
  });

  it('sends the event anyway when nothing says where it came from', async () => {
    await POST(
      request({ 'x-umami-website-id': 'w-1', 'user-agent': 'Mozilla/5.0 (iPhone)' }),
    );

    expect(sent().body).toEqual(EVENT);
  });
});

describe('the event survives', () => {
  it('forwards a body it cannot parse rather than dropping it', async () => {
    await POST(request(VISITOR, 'not json at all'));

    expect(sent().raw).toBe('not json at all');
  });

  it('answers the tracker even when Umami is unreachable', async () => {
    fetchMock.mockRejectedValue(new Error('ECONNRESET'));

    await expect(POST(request(VISITOR))).resolves.toMatchObject({ status: 204 });
  });
});
