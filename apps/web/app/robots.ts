import type { MetadataRoute } from 'next';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      // Every route lives under a locale segment (/en/cart, /ar/cart), so the
      // bare prefixes matched nothing. Keep both shapes: the unprefixed ones
      // still cover any legacy link, the wildcard ones cover what we serve.
      {
        userAgent: '*',
        allow: '/',
        disallow: [
          '/account',
          '/checkout',
          '/cart',
          '/*/account',
          '/*/checkout',
          '/*/cart',
        ],
      },
      // AI / LLM crawlers.
      //
      // Named individually even though `User-Agent: *` above already allows
      // them, because these are the agents whose access is a *decision* rather
      // than an accident — a future "block the AI crawlers" is an edit to this
      // list, and a list that names the current agents is one somebody can
      // actually act on. This shop wants to be quoted: the answer to "who
      // delivers brownies in Sharjah" is worth more than the page view it
      // replaces, which is also why `/llms.txt` exists.
      //
      // Each vendor now runs several agents for different purposes, and they
      // are not interchangeable — training, live retrieval for a user's
      // question, and search indexing are three separate crawlers. Missing one
      // of them means being absent from that surface specifically.

      // OpenAI: training / user-initiated fetch / ChatGPT search indexing.
      { userAgent: 'GPTBot', allow: '/' },
      { userAgent: 'ChatGPT-User', allow: '/' },
      { userAgent: 'OAI-SearchBot', allow: '/' },
      // Anthropic. `ClaudeBot` is the current crawler; `Claude-User` fetches a
      // page because somebody asked about it, and `Claude-SearchBot` indexes
      // for search. `anthropic-ai` and `Claude-Web` are the retired names, kept
      // because an old agent still reading them costs nothing.
      { userAgent: 'ClaudeBot', allow: '/' },
      { userAgent: 'Claude-User', allow: '/' },
      { userAgent: 'Claude-SearchBot', allow: '/' },
      { userAgent: 'Claude-Web', allow: '/' },
      { userAgent: 'anthropic-ai', allow: '/' },
      // Perplexity: crawler and user-initiated fetch.
      { userAgent: 'PerplexityBot', allow: '/' },
      { userAgent: 'Perplexity-User', allow: '/' },
      // Google. `Google-Extended` is not a crawler — it is the token that says
      // whether Googlebot's existing crawl may feed Gemini and AI Overviews.
      { userAgent: 'Google-Extended', allow: '/' },
      // Apple: `Applebot` serves Siri and Spotlight, `-Extended` is the
      // training token. Only the latter was listed, so the one that answers a
      // "hey Siri, brownies near me" was left to the wildcard.
      { userAgent: 'Applebot', allow: '/' },
      { userAgent: 'Applebot-Extended', allow: '/' },
      // Meta: crawler and the fetch made when a link is shared or asked about.
      { userAgent: 'FacebookBot', allow: '/' },
      { userAgent: 'Meta-ExternalAgent', allow: '/' },
      { userAgent: 'Meta-ExternalFetcher', allow: '/' },
      { userAgent: 'MistralAI-User', allow: '/' },
      { userAgent: 'DuckAssistBot', allow: '/' },
      { userAgent: 'Amazonbot', allow: '/' },
      { userAgent: 'Bytespider', allow: '/' },
      { userAgent: 'YouBot', allow: '/' },
      { userAgent: 'CCBot', allow: '/' },
      { userAgent: 'cohere-ai', allow: '/' },
      { userAgent: 'cohere-training-data-crawler', allow: '/' },
    ],
    sitemap: [
      `${SITE_URL}/sitemap.xml`,
      `${SITE_URL}/image-sitemap.xml`,
    ],
  };
}
