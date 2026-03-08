import type { Metadata } from 'next';
import Link from 'next/link';
import { cmsApi } from '@/lib/api';
import { FaqAccordion } from './FaqAccordion';

interface FaqItem {
  question: string;
  answer: string;
}

interface FaqContent {
  header?: { title?: string; subtitle?: string };
  items?: FaqItem[];
  cta?: { title?: string; subtitle?: string; whatsapp_text?: string; contact_text?: string };
  seo?: { title?: string; description?: string };
}

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  try {
    const page = await cmsApi.getPage('faq', locale);
    const c = page.content as FaqContent;
    return {
      title: c.seo?.title ?? 'FAQ',
      description: c.seo?.description ?? '',
      alternates: {
        canonical: `${SITE_URL}/${locale}/faq`,
        languages: { en: `${SITE_URL}/en/faq`, ar: `${SITE_URL}/ar/faq` },
      },
      openGraph: {
        title: `${c.seo?.title ?? 'FAQ'} | Melting Moments Cakes`,
        description: c.seo?.description ?? '',
      },
    };
  } catch {
    return { title: 'FAQ' };
  }
}

export default async function FaqPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  let c: FaqContent = {};
  try {
    const page = await cmsApi.getPage('faq', locale);
    c = page.content as FaqContent;
  } catch {
    // fallback to empty
  }

  const items = c.items ?? [];

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: items.map(({ question, answer }) => ({
      '@type': 'Question',
      name: question,
      acceptedAnswer: { '@type': 'Answer', text: answer },
    })),
  };

  const faqs = items.map(item => ({ q: item.question, a: item.answer }));

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* Header */}
      <div className="bg-[#f9f5f0] border-b border-secondary/30">
        <div className="max-w-3xl mx-auto px-4 py-14 text-center">
          <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">Help Centre</p>
          <h1 className="font-display text-4xl sm:text-5xl text-primary mb-4">
            {c.header?.title ?? ''}
          </h1>
          <p className="font-body text-sm text-gray-500 max-w-md mx-auto">
            {c.header?.subtitle ?? ''}
          </p>
        </div>
      </div>

      {/* FAQ List */}
      <div className="max-w-3xl mx-auto px-4 py-14">
        {faqs.length > 0 && <FaqAccordion faqs={faqs} />}

        {/* CTA */}
        <div className="mt-12 text-center border border-secondary/40 py-10 px-6 bg-[#f9f5f0]">
          <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">Still have questions?</p>
          <h2 className="font-display text-2xl text-primary mb-3">{c.cta?.title ?? ''}</h2>
          <p className="font-body text-sm text-gray-500 mb-6 max-w-sm mx-auto">
            {c.cta?.subtitle ?? ''}
          </p>
          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <a
              href="https://wa.me/971503687757"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
            >
              <span className="material-icons text-[16px]">chat</span>
              {c.cta?.whatsapp_text ?? 'WhatsApp Us'}
            </a>
            <Link
              href="/contact"
              className="inline-flex items-center justify-center gap-2 px-6 py-3 border border-primary text-primary text-xs font-body uppercase tracking-widest hover:bg-primary hover:text-white transition-all duration-200"
            >
              <span className="material-icons text-[16px]">mail</span>
              {c.cta?.contact_text ?? 'Contact Form'}
            </Link>
          </div>
        </div>
      </div>
    </>
  );
}
