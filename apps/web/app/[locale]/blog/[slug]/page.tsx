import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import type { BlogPost } from '@/lib/types';
import { Breadcrumb } from '@/components/ui';
import { API_BASE } from '@/lib/api';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

async function fetchPost(slug: string, locale: string): Promise<BlogPost | null> {
  try {
    const res = await fetch(
      `${API_BASE}/blog/public/${slug}?locale=${locale}`,
      { next: { revalidate: 300 }, signal: AbortSignal.timeout(8000) },
    );
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}): Promise<Metadata> {
  const { locale, slug } = await params;
  const post = await fetchPost(slug, locale);
  if (!post) return { title: 'Post Not Found' };

  const c = post.content;
  const title = c.title ?? slug;
  const description = c.meta_description ?? c.excerpt ?? '';
  const ogImages = c.cover_image
    ? [{ url: c.cover_image, alt: title }]
    : [{ url: '/images/logos/color_logo.jpeg', alt: 'Melting Moments Cakes' }];

  return {
    title,
    description,
    alternates: {
      canonical: `${SITE_URL}/${locale}/blog/${slug}`,
      languages: {
        en: `${SITE_URL}/en/blog/${slug}`,
        ar: `${SITE_URL}/ar/blog/${slug}`,
      },
    },
    openGraph: {
      title: `${title} | Melting Moments Cakes`,
      description,
      images: ogImages,
      type: 'article',
      publishedTime: post.created_at,
      modifiedTime: post.updated_at,
    },
  };
}

/** Render a plain-text body with ## headings and paragraph breaks */
function BlogBody({ body }: { body: string }) {
  const blocks = body.split('\n\n').filter(Boolean);
  return (
    <div className="space-y-5">
      {blocks.map((block, i) => {
        if (block.startsWith('## ')) {
          return (
            <h2 key={i} className="font-display text-2xl text-primary mt-8 mb-2">
              {block.replace(/^## /, '')}
            </h2>
          );
        }
        if (block.startsWith('# ')) {
          return (
            <h2 key={i} className="font-display text-3xl text-primary mt-8 mb-2">
              {block.replace(/^# /, '')}
            </h2>
          );
        }
        return (
          <p key={i} className="font-body text-sm text-gray-600 leading-relaxed whitespace-pre-line">
            {block}
          </p>
        );
      })}
    </div>
  );
}

function formatDate(iso: string, locale: string): string {
  try {
    return new Date(iso).toLocaleDateString(locale === 'ar' ? 'ar-AE' : 'en-AE', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch {
    return iso.slice(0, 10);
  }
}

export default async function BlogPostPage({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  const { locale, slug } = await params;
  const post = await fetchPost(slug, locale);
  if (!post) notFound();

  const c = post.content;
  const title = c.title ?? slug;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_URL}/${locale}` },
          { '@type': 'ListItem', position: 2, name: 'Blog', item: `${SITE_URL}/${locale}/blog` },
          { '@type': 'ListItem', position: 3, name: title, item: `${SITE_URL}/${locale}/blog/${slug}` },
        ],
      },
      {
        '@type': 'Article',
        headline: title,
        description: c.meta_description ?? c.excerpt ?? '',
        datePublished: post.created_at,
        dateModified: post.updated_at,
        author: {
          '@type': 'Person',
          name: 'Fatema Abbasi',
          url: `${SITE_URL}/${locale}/about`,
        },
        publisher: {
          '@type': 'Organization',
          name: 'Melting Moments Cakes',
          url: SITE_URL,
          logo: { '@type': 'ImageObject', url: `${SITE_URL}/images/logos/color_logo.jpeg` },
        },
        url: `${SITE_URL}/${locale}/blog/${slug}`,
        ...(c.cover_image ? { image: c.cover_image } : {}),
        ...(c.tags && c.tags.length > 0 ? { keywords: c.tags.join(', ') } : {}),
      },
    ],
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <div className="max-w-3xl mx-auto px-4 py-12">
        <Breadcrumb
          items={[
            { label: 'Home', href: `/${locale}` },
            { label: 'Blog', href: `/${locale}/blog` },
            { label: title },
          ]}
        />

        <article>
          {/* Meta */}
          <header className="mb-8">
            <p className="font-body text-[11px] uppercase tracking-widest text-secondary mb-4">
              {formatDate(post.created_at, locale)}
            </p>
            <h1 className="font-display text-3xl sm:text-4xl text-primary leading-snug mb-4">
              {title}
            </h1>
            {c.excerpt && (
              <p className="font-body text-base text-gray-500 leading-relaxed border-l-2 border-secondary pl-4">
                {c.excerpt}
              </p>
            )}
            {c.tags && c.tags.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-5">
                {c.tags.map((tag) => (
                  <span
                    key={tag}
                    className="font-body text-[10px] uppercase tracking-widest border border-secondary/50 text-secondary px-2 py-0.5"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
            <div className="h-px bg-secondary/40 mt-6" />
          </header>

          {/* Body */}
          {c.body && <BlogBody body={c.body} />}
        </article>

        {/* Back link */}
        <div className="mt-12 pt-8 border-t border-gray-100">
          <Link
            href={`/${locale}/blog`}
            className="font-body text-xs uppercase tracking-widest text-primary hover:underline inline-flex items-center gap-1"
          >
            ← Back to Journal
          </Link>
        </div>
      </div>
    </>
  );
}
