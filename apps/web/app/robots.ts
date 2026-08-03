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
      // AI / LLM crawlers
      { userAgent: 'GPTBot', allow: '/' },
      { userAgent: 'ChatGPT-User', allow: '/' },
      { userAgent: 'Claude-Web', allow: '/' },
      { userAgent: 'PerplexityBot', allow: '/' },
      { userAgent: 'Applebot-Extended', allow: '/' },
      { userAgent: 'Amazonbot', allow: '/' },
      { userAgent: 'Google-Extended', allow: '/' },
      { userAgent: 'Bytespider', allow: '/' },
      { userAgent: 'YouBot', allow: '/' },
      { userAgent: 'CCBot', allow: '/' },
      { userAgent: 'cohere-ai', allow: '/' },
      { userAgent: 'anthropic-ai', allow: '/' },
      { userAgent: 'FacebookBot', allow: '/' },
      { userAgent: 'Meta-ExternalAgent', allow: '/' },
    ],
    sitemap: [
      `${SITE_URL}/sitemap.xml`,
      `${SITE_URL}/image-sitemap.xml`,
    ],
  };
}
