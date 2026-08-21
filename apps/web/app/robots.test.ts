/**
 * Who is allowed to read the shop, and who is named while being allowed.
 *
 * `User-Agent: *` already permits every one of these, so nothing here changes
 * what a crawler may fetch today. Naming them is what makes the permission a
 * decision instead of an accident: a future "stop feeding the AI crawlers" is
 * an edit to this list, and a list naming agents that were retired two years
 * ago is one nobody can act on.
 *
 * The trap this guards is that each vendor runs several agents for different
 * jobs — training, fetching a page because a user asked about it, and indexing
 * for search — and they are not interchangeable. Missing one means being absent
 * from that surface specifically, silently, with the wildcard making it look
 * fine.
 */

import { describe, expect, it } from 'vitest';

import robots from './robots';

const agents = () =>
  (robots().rules as { userAgent: string }[]).map((r) => r.userAgent);

describe('robots.txt', () => {
  it('names the current agent of every major answer engine', () => {
    // The ones that were missing: `ClaudeBot` superseded `anthropic-ai` and
    // `Claude-Web`, and `OAI-SearchBot` is what powers ChatGPT search — neither
    // was listed, so the file named only retired and training-only agents.
    for (const agent of [
      'GPTBot',
      'OAI-SearchBot',
      'ClaudeBot',
      'Claude-SearchBot',
      'PerplexityBot',
      'Google-Extended',
      'Applebot',
    ]) {
      expect(agents()).toContain(agent);
    }
  });

  it('names the user-initiated fetchers, not just the training crawlers', () => {
    // These fire when somebody asks an assistant about this shop — the traffic
    // this site most wants, and a different agent from the training crawler.
    for (const agent of ['ChatGPT-User', 'Claude-User', 'Perplexity-User']) {
      expect(agents()).toContain(agent);
    }
  });

  it('keeps the retired names rather than replacing them', () => {
    // An old agent still reading these costs nothing, and dropping them is a
    // silent loss of access for anything that has not been updated.
    expect(agents()).toContain('anthropic-ai');
    expect(agents()).toContain('Claude-Web');
  });

  it('does not try to hide the basket, the checkout or the account here', () => {
    // These were `Disallow`, and that is the wrong instrument: it forbids the
    // fetch, not the listing. The basket is linked from the header of every
    // page, so its URL is well known — and a URL a crawler may not read is one
    // it can only index as a naked link, with no title and nothing to say. Bing
    // reported `/en/cart` for precisely that.
    //
    // Keeping them out is `robots: { index: false, follow: false }` on the
    // routes, in the `layout.tsx` beside each page, and a crawler can only obey
    // a directive it was allowed to fetch. Blocking here would put the old
    // failure straight back.
    const wildcard = (
      robots().rules as { userAgent: string; allow?: string; disallow?: string[] }[]
    ).find((r) => r.userAgent === '*')!;
    expect(wildcard.disallow ?? []).toEqual([]);
    expect(wildcard.allow).toBe('/');
  });

  it('points at both sitemaps', () => {
    expect(robots().sitemap).toHaveLength(2);
  });
});
