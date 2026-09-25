/**
 * Apple Pay's proof that this domain may take Apple Pay for a merchant.
 *
 * Apple fetches this path when a gateway registers the domain for its Apple
 * Pay merchant, and the file it expects is issued by that gateway — so it is an
 * environment variable (`PAYMOB_APPLE_DOMAIN_ASSOCIATION`, the file's exact
 * contents) rather than a file in the repo, and it is served only while it is
 * set. Unset — which production is until the domain is registered — the path
 * is a 404, the same as it was before this route existed, so nothing claims an
 * association that has not been made.
 *
 * Stripe's Apple Pay verifies its domain the same way, but through Stripe's
 * dashboard, which never needed this route; the embedded-SDK Apple Pay does.
 *
 * Read per request (`force-dynamic`), not baked in at build, so the answer is
 * always the environment the deployment is actually running with. Outside the
 * locale middleware's matcher (the path contains a dot), so it is served as-is.
 */
export const dynamic = 'force-dynamic';

export function GET() {
  const association = process.env.PAYMOB_APPLE_DOMAIN_ASSOCIATION;
  if (!association) {
    return new Response('Not found', {
      status: 404,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  }

  return new Response(association, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
    },
  });
}
