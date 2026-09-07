/**
 * The page that has to render when everything above it — including the root
 * layout's data fetches — has thrown.
 *
 * It used to be `<NextError statusCode={0} />` from `next/error`, which on
 * this deploy target reaches for a static `pages/500.html` this app has never
 * had and throws a second time trying to render the error page at all. A
 * three-hour outage never showed a customer anything branded because of it.
 * This asserts the replacement never imports `next/error` and renders cleanly
 * with nothing but React and Sentry.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const captureException = vi.fn();
vi.mock('@sentry/nextjs', () => ({ captureException: (...args: unknown[]) => captureException(...args) }));

describe('global-error', () => {
  it('does not import next/error', () => {
    const source = readFileSync(join(__dirname, 'global-error.tsx'), 'utf-8');
    expect(source).not.toMatch(/from ["']next\/error["']/);
  });

  it('renders its own branded <html>/<body> without throwing', async () => {
    const { default: GlobalError } = await import('./global-error');
    const error = Object.assign(new Error('boom'), { digest: 'abc123' });

    expect(() => render(<GlobalError error={error} />)).not.toThrow();
    expect(screen.getByText('Something Went Wrong')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /back to home/i })).toBeInTheDocument();
  });

  it('reports the error to Sentry', async () => {
    captureException.mockClear();
    const { default: GlobalError } = await import('./global-error');
    const error = Object.assign(new Error('boom'), { digest: 'abc123' });

    render(<GlobalError error={error} />);

    expect(captureException).toHaveBeenCalledWith(error);
  });
});
