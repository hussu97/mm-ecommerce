import type { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';
import { branchesApi, cmsApi } from '@/lib/api-server';
import { Breadcrumb } from '@/components/ui';
import { BUSINESS_ID, FOUNDER, FOUNDER_ID } from '@/lib/schema';
import { Icon } from '@/components/ui/Icon';

interface Value {
  icon: string;
  title: string;
  description: string;
}

interface AboutContent {
  hero?: { label?: string; title?: string; subtitle?: string };
  story_1?: { label?: string; title?: string; body?: string; image_url?: string };
  story_2?: { label?: string; title?: string; body?: string; image_url?: string; cta_text?: string };
  values?: Value[];
  values_section?: { label?: string; title?: string };
  cta?: { title?: string; subtitle?: string; button_text?: string; button_link?: string };
  /** Heading for the collection-points block. The addresses are not here — see below. */
  find_us?: { label?: string; title?: string; subtitle?: string };
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
    const page = await cmsApi.getPage('about', locale);
    const c = page.content as AboutContent;
    return {
      title: c.seo?.title ?? 'About Me',
      description: c.seo?.description ?? '',
      alternates: {
        canonical: `${SITE_URL}/${locale}/about`,
        languages: {
          en: `${SITE_URL}/en/about`,
          ar: `${SITE_URL}/ar/about`,
          'x-default': `${SITE_URL}/en/about`,
        },
      },
      openGraph: {
        title: `${c.seo?.title ?? 'About Me'} | Melting Moments Cakes`,
        description: c.seo?.description ?? '',
        images: [{ url: '/images/photos/person_shot_1.jpg' }],
      },
    };
  } catch {
    return { title: 'About Me' };
  }
}

export default async function AboutPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  let c: AboutContent = {};
  try {
    const page = await cmsApi.getPage('about', locale);
    c = page.content as AboutContent;
  } catch {
    // fallback to empty — page will still render with empty strings
  }

  // Never throws: `pickupPoints` resolves to `[]` on any failure, and the
  // section below is skipped when it is empty.
  const pickupPoints = await branchesApi.pickupPoints();
  const isArabic = locale === 'ar';

  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        ...FOUNDER,
        description:
          'Fatema Abbasi founded Melting Moments Cakes and still does the baking herself, in the Sharjah kitchen.',
        worksFor: { '@id': BUSINESS_ID },
        image: `${SITE_URL}/images/photos/person_shot_1.jpg`,
      },
      {
        '@type': 'AboutPage',
        '@id': `${SITE_URL}/${locale}/about`,
        name: 'About Melting Moments Cakes',
        description:
          'How a bakery in Sharjah grew from baking for family into brownie, cookie and dessert delivery across the UAE.',
        about: { '@id': BUSINESS_ID },
        mainEntity: { '@id': FOUNDER_ID },
      },
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_URL}/${locale}` },
          { '@type': 'ListItem', position: 2, name: 'About', item: `${SITE_URL}/${locale}/about` },
        ],
      },
    ],
  };

  const values = c.values ?? [];

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* Hero */}
      <section className="relative h-[480px] sm:h-[560px]">
        <Image
          src="/images/photos/person_shot_1.jpg"
          alt="Fatema Abbasi, founder and baker at Melting Moments Cakes, in her kitchen in Sharjah"
          fill
          sizes="100vw"
          className="object-cover object-top"
          priority
        />
        <div className="absolute inset-0 bg-primary/70" />
        <div className="relative z-10 h-full flex flex-col items-center justify-center px-4 text-center text-white">
          {c.hero?.label && (
            <span className="inline-block border border-white/60 text-[11px] font-body uppercase tracking-[0.3em] px-5 py-2 mb-5">
              {c.hero.label}
            </span>
          )}
          <h1 className="font-display text-4xl sm:text-5xl lg:text-6xl leading-tight mb-4">
            {c.hero?.title ?? ''}
          </h1>
          <p className="font-body text-base text-white/80 max-w-lg">
            {c.hero?.subtitle ?? ''}
          </p>
        </div>
      </section>

      {/* Story Section 1 — text + photo */}
      <section className="max-w-6xl mx-auto px-4 py-16 lg:py-24">
        <Breadcrumb items={[{ label: 'Home', href: `/${locale}` }, { label: 'About' }]} />
        <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
          <div>
            {c.story_1?.label && (
              <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">{c.story_1.label}</p>
            )}
            <h2 className="font-display text-3xl sm:text-4xl text-primary mb-6 leading-snug">
              {c.story_1?.title ?? ''}
            </h2>
            {/* Blank lines separate paragraphs; a single newline is a line
                break inside one. The story copy uses both — "The photo everyone
                takes. / The bite everyone remembers." is three lines of one
                thought, and running them together loses the rhythm they were
                written for. */}
            <div className="space-y-4 text-sm font-body text-gray-600 leading-relaxed whitespace-pre-line">
              {(c.story_1?.body ?? '').split('\n\n').filter(Boolean).map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          </div>
          <div className="relative">
            <div className="relative aspect-[4/5] overflow-hidden">
              <Image
                src={c.story_1?.image_url ?? '/images/photos/person_shot_2.png'}
                alt="Fatema cutting a tray of fudgy chocolate brownies in the Melting Moments kitchen"
                fill
                sizes="(max-width: 1024px) 100vw, 50vw"
                className="object-cover object-center"
              />
            </div>
            <div className="absolute -bottom-3 -right-3 w-full aspect-[4/5] border-2 border-secondary pointer-events-none" />
          </div>
        </div>
      </section>

      {/* Story Section 2 — photo + text */}
      <section className="bg-[#f9f5f0]">
        <div className="max-w-6xl mx-auto px-4 py-16 lg:py-24">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
            <div className="order-2 lg:order-1 relative">
              <div className="relative aspect-[4/5] overflow-hidden">
                <Image
                  src={c.story_2?.image_url ?? '/images/photos/person_shot_3.png'}
                  alt="A boxed order of brownies and cookies ready for delivery across the UAE"
                  fill
                  sizes="(max-width: 1024px) 100vw, 50vw"
                  className="object-cover object-center"
                />
              </div>
              <div className="absolute -bottom-3 -left-3 w-full aspect-[4/5] border-2 border-secondary pointer-events-none" />
            </div>
            <div className="order-1 lg:order-2">
              {c.story_2?.label && (
                <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">{c.story_2.label}</p>
              )}
              <h2 className="font-display text-3xl sm:text-4xl text-primary mb-6 leading-snug">
                {c.story_2?.title ?? ''}
              </h2>
              <div className="space-y-4 text-sm font-body text-gray-600 leading-relaxed whitespace-pre-line">
                {(c.story_2?.body ?? '').split('\n\n').filter(Boolean).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
              {c.story_2?.cta_text && (
                <Link
                  href="/contact"
                  className="inline-flex items-center gap-2 mt-6 text-xs font-body uppercase tracking-widest text-primary border border-primary px-5 py-2.5 hover:bg-primary hover:text-white transition-all duration-200"
                >
                  {c.story_2.cta_text}
                </Link>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* Values */}
      {values.length > 0 && (
        <section className="max-w-6xl mx-auto px-4 py-16 lg:py-24">
          <div className="text-center mb-12">
            {c.values_section?.label && (
              <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">
                {c.values_section.label}
              </p>
            )}
            {c.values_section?.title && (
              <h2 className="font-display text-3xl sm:text-4xl text-primary">
                {c.values_section.title}
              </h2>
            )}
            <div className="h-px bg-secondary/40 max-w-xs mx-auto mt-5" />
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {values.map(({ icon, title, description }) => (
              <div
                key={title}
                className="border border-gray-200 hover:border-primary p-6 text-center transition-colors group"
              >
                <Icon name={icon} className="text-3xl text-secondary group-hover:text-primary mb-4 block transition-colors" />
                <h3 className="font-body text-sm font-medium uppercase tracking-widest text-gray-800 mb-2">
                  {title}
                </h3>
                <p className="font-body text-xs text-gray-500 leading-relaxed">{description}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Where to collect.
          The copy is CMS; the addresses are the branch rows, fetched. Writing
          an address into the copy would be a second answer to a question the
          branch record already settles, and the day the shop moves one of the
          two is wrong with nothing to say so. "Where to collect" rather than
          "our locations" because one of these is a partner counter. */}
      {pickupPoints.length > 0 && (
        <section className="max-w-6xl mx-auto px-4 py-16 lg:py-24">
          <div className="text-center mb-12">
            {c.find_us?.label && (
              <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">
                {c.find_us.label}
              </p>
            )}
            <h2 className="font-display text-3xl sm:text-4xl text-primary">
              {c.find_us?.title ?? 'Where to collect'}
            </h2>
            {c.find_us?.subtitle && (
              <p className="font-body text-sm text-gray-500 mt-4 max-w-xl mx-auto">
                {c.find_us.subtitle}
              </p>
            )}
            <div className="h-px bg-secondary/40 max-w-xs mx-auto mt-5" />
          </div>

          <div className="grid sm:grid-cols-2 gap-6 max-w-3xl mx-auto">
            {pickupPoints.map(point => {
              const name = (isArabic && point.name_ar) || point.name;
              const address = (isArabic && point.address_ar) || point.address;
              return (
                <div key={point.id} className="border border-gray-200 p-6">
                  <h3 className="font-body text-sm font-medium uppercase tracking-widest text-gray-800 mb-3">
                    {name}
                  </h3>
                  {address && (
                    <p className="font-body text-sm text-gray-600 leading-relaxed mb-3">
                      {address}
                    </p>
                  )}
                  <p className="font-body text-xs text-gray-400 mb-4">
                    {point.opening_from} – {point.opening_to}
                  </p>
                  {point.maps_url && (
                    <a
                      href={point.maps_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1.5 font-body text-xs uppercase tracking-widest text-primary hover:underline"
                    >
                      <Icon name="place" className="text-[16px]" />
                      {isArabic ? 'الاتجاهات' : 'Directions'}
                    </a>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* CTA Banner */}
      <section className="relative overflow-hidden">
        <Image
          src="/images/photos/person_shot_4.jpg"
          alt="Brownies, cookies and cookie melts from Melting Moments Cakes, Sharjah"
          fill
          sizes="100vw"
          className="object-cover object-center"
        />
        <div className="absolute inset-0 bg-primary/75" />
        <div className="relative z-10 py-20 px-4 text-center text-white">
          <h2 className="font-display text-3xl sm:text-4xl mb-4">{c.cta?.title ?? ''}</h2>
          <p className="font-body text-sm text-white/80 mb-8 max-w-md mx-auto">
            {c.cta?.subtitle ?? ''}
          </p>
          <Link
            href={c.cta?.button_link ?? '/shop'}
            className="inline-flex items-center gap-2 font-body text-xs uppercase tracking-widest bg-white text-primary px-8 py-3.5 hover:bg-white/90 transition-all duration-200"
          >
            {c.cta?.button_text ?? 'Shop Now'}
            <Icon name="arrow_forward" className="text-[16px]" />
          </Link>
        </div>
      </section>
    </>
  );
}
