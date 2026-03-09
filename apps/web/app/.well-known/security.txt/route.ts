export function GET() {
  const body = `Contact: mailto:hello@meltingmomentscakes.com
Preferred-Languages: en, ar
Canonical: https://meltingmomentscakes.com/.well-known/security.txt
Expires: 2027-03-09T00:00:00.000Z
`;

  return new Response(body, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=86400',
    },
  });
}
