import type { Metadata } from 'next';
import { cmsApi } from '@/lib/api';

interface Section {
  title: string;
  body: string;
}

interface PrivacyContent {
  header?: { title?: string; subtitle?: string };
  intro?: string;
  sections?: Section[];
  contact?: { email?: string; phone?: string };
  seo?: { title?: string; description?: string };
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  try {
    const page = await cmsApi.getPage('privacy', locale);
    const c = page.content as PrivacyContent;
    return {
      title: c.seo?.title ?? 'Privacy Policy',
      description: c.seo?.description ?? '',
    };
  } catch {
    return { title: 'Privacy Policy' };
  }
}

export default async function PrivacyPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  let c: PrivacyContent = {};
  try {
    const page = await cmsApi.getPage('privacy', locale);
    c = page.content as PrivacyContent;
  } catch {
    // fallback to empty
  }

  const sections = c.sections ?? [];

  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      {/* Header */}
      <h1 className="font-display text-3xl sm:text-4xl text-primary mb-2">
        {c.header?.title ?? 'Privacy Policy'}
      </h1>
      {c.header?.subtitle && (
        <p className="font-body text-xs text-gray-400 uppercase tracking-widest mb-6">
          {c.header.subtitle}
        </p>
      )}
      <div className="h-px bg-secondary/40 mb-8" />

      {/* Intro */}
      {c.intro && (
        <div className="mb-10">
          {c.intro.split('\n\n').filter(Boolean).map((p, i) => (
            <p key={i} className="font-body text-sm text-gray-600 leading-relaxed mb-4 last:mb-0">
              {p}
            </p>
          ))}
        </div>
      )}

      {/* Sections */}
      {sections.length > 0 && (
        <div className="space-y-10">
          {sections.map((section, i) => (
            <div key={i}>
              {section.title && (
                <h2 className="font-display text-xl text-primary mb-4">{section.title}</h2>
              )}
              <div className="space-y-3">
                {section.body.split('\n\n').filter(Boolean).map((para, j) => (
                  <p key={j} className="font-body text-sm text-gray-600 leading-relaxed whitespace-pre-line">
                    {para}
                  </p>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Contact */}
      {(c.contact?.email || c.contact?.phone) && (
        <div className="mt-12 border border-secondary/40 bg-[#f9f5f0] p-6">
          <h2 className="font-display text-xl text-primary mb-4">Contact Us</h2>
          <div className="space-y-2">
            {c.contact.email && (
              <p className="font-body text-sm text-gray-600">
                By email:{' '}
                <a href={`mailto:${c.contact.email}`} className="text-primary hover:underline">
                  {c.contact.email}
                </a>
              </p>
            )}
            {c.contact.phone && (
              <p className="font-body text-sm text-gray-600">
                By phone:{' '}
                <a href={`tel:${c.contact.phone.replace(/\s/g, '')}`} className="text-primary hover:underline">
                  {c.contact.phone}
                </a>
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
