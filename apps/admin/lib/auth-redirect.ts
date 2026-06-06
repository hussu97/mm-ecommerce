export function safeNextPath(search: string): string {
  const next = new URLSearchParams(search).get('next');
  if (!next || !next.startsWith('/') || next.startsWith('//')) return '/';
  return next;
}

export function loginPathFor(pathname: string, search = ''): string {
  const next = `${pathname}${search}`;
  return `/login?next=${encodeURIComponent(next)}`;
}
