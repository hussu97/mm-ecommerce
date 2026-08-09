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

  it("still keeps crawlers out of the pages that are nobody else's business", () => {
    const wildcard = (robots().rules as { userAgent: string; disallow?: string[] }[]).find(
      (r) => r.userAgent === '*',
    )!;
    // Both shapes, because every route lives under a locale segment and the
    // bare prefixes on their own matched nothing we actually serve.
    for (const path of ['/account', '/checkout', '/cart', '/*/account', '/*/checkout', '/*/cart']) {
      expect(wildcard.disallow).toContain(path);
    }
  });

  it('points at both sitemaps', () => {
    expect(robots().sitemap).toHaveLength(2);
  });
});
