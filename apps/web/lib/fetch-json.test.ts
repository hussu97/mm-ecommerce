import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchJsonOrNull } from './fetch-json';

type Step = { status: number; body?: string } | 'network';

/** A fetch mock that walks a scripted list of outcomes, one per attempt. */
function scripted(steps: Step[]) {
  let i = 0;
  return vi.fn(async () => {
    const step = steps[Math.min(i, steps.length - 1)];
    i += 1;
    if (step === 'network') throw new Error('ECONNREFUSED');
    return new Response(step.body ?? '', { status: step.status });
  });
}

describe('fetchJsonOrNull — transient retry', () => {
  afterEach(() => vi.restoreAllMocks());

  it('retries a 5xx and returns the body once the API recovers', async () => {
    const f = scripted([
      { status: 503 },
      { status: 503 },
      { status: 200, body: JSON.stringify({ ok: 1 }) },
    ]);
    vi.stubGlobal('fetch', f);

    const out = await fetchJsonOrNull<{ ok: number }>('https://x/api/v1/products/p');

    expect(out).toEqual({ ok: 1 });
    expect(f).toHaveBeenCalledTimes(3);
  });

  it('retries a network-level failure before it recovers', async () => {
    const f = scripted(['network', { status: 200, body: JSON.stringify({ ok: 2 }) }]);
    vi.stubGlobal('fetch', f);

    const out = await fetchJsonOrNull<{ ok: number }>('https://x/api/v1/products/p');

    expect(out).toEqual({ ok: 2 });
    expect(f).toHaveBeenCalledTimes(2);
  });

  it('does not retry a 404 — it is a real answer, not a blip', async () => {
    const f = scripted([{ status: 404 }]);
    vi.stubGlobal('fetch', f);

    const out = await fetchJsonOrNull('https://x/api/v1/products/gone');

    expect(out).toBeNull();
    expect(f).toHaveBeenCalledTimes(1);
  });
});
