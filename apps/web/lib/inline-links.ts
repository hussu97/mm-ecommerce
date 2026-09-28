/**
 * Links inside CMS copy that is otherwise plain text — an FAQ answer.
 *
 * The one syntax is Markdown's `[label](href)`, because it is what an editor
 * typing into a plain textarea already knows, and it degrades to readable text
 * anywhere it is not parsed. Only site paths (`/contact`) and `https:` / `mailto:`
 * / `tel:` targets become links; anything else is left as the words it was, so
 * a pasted `javascript:` URL can never render as something to click.
 */

export type InlineSegment =
  | { kind: 'text'; text: string }
  | { kind: 'link'; text: string; href: string; internal: boolean };

const LINK = /\[([^\]\n]+)\]\(([^)\s]+)\)/g;

function isSafe(href: string): { ok: boolean; internal: boolean } {
  if (href.startsWith('/') && !href.startsWith('//')) return { ok: true, internal: true };
  return { ok: /^(https:|mailto:|tel:)/i.test(href), internal: false };
}

export function parseInlineLinks(source: string): InlineSegment[] {
  const out: InlineSegment[] = [];
  let last = 0;
  for (const match of source.matchAll(LINK)) {
    const [whole, text, href] = match;
    const start = match.index ?? 0;
    const safe = isSafe(href);
    if (!safe.ok) continue;
    if (start > last) out.push({ kind: 'text', text: source.slice(last, start) });
    out.push({ kind: 'link', text, href, internal: safe.internal });
    last = start + whole.length;
  }
  if (last < source.length) out.push({ kind: 'text', text: source.slice(last) });
  return out;
}

/** The copy with every link reduced to its label — for structured data and
 * anywhere else that wants the sentence, not the markup. */
export function stripInlineLinks(source: string): string {
  return parseInlineLinks(source)
    .map(s => s.text)
    .join('');
}
