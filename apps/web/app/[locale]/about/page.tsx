import type { Metadata } from 'next';
import Image from 'next/image';
import Link from 'next/link';

export const metadata: Metadata = {
  title: 'About Me',
  description:
    'Meet Fatema Abbasi — the baker behind Melting Moments. Handcrafted brownies, cookies and desserts made with love from her home kitchen in the UAE.',
  openGraph: {
    title: 'About Me | Melting Moments Cakes',
    description: 'Meet the baker behind Melting Moments — handcrafted sweets made with 100% love.',
    images: [{ url: '/images/photos/person_shot_1.jpg' }],
  },
};

const VALUES = [
  {
    icon: 'favorite',
    title: 'Made with Love',
    description:
      'Every item is baked fresh to order. No preservatives, no shortcuts — just honest, heartfelt baking.',
  },
  {
    icon: 'eco',
    title: 'Quality Ingredients',
    description:
      'We source the finest ingredients — premium chocolate, real butter, and fresh produce for every batch.',
  },
  {
    icon: 'diversity_3',
    title: 'For Every Occasion',
    description:
      'Birthdays, weddings, Eid, corporate gifting — we craft something special for every moment worth celebrating.',
  },
  {
    icon: 'local_shipping',
    title: 'Delivered Across UAE',
    description:
      'We deliver to Dubai, Sharjah, Ajman and beyond. Orders are packed carefully to arrive picture-perfect.',
  },
];

export default function AboutPage() {
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Person',
    name: 'Fatema Abbasi',
    jobTitle: 'Founder & Baker',
    worksFor: {
      '@type': 'LocalBusiness',
      name: 'Melting Moments Cakes',
      url: process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com',
    },
    sameAs: ['https://www.instagram.com/meltingmomentscakes'],
  };

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
          alt="Fatema Abbasi — baker at Melting Moments"
          fill
          sizes="100vw"
          className="object-cover object-top"
          priority
        />
        <div className="absolute inset-0 bg-primary/70" />
        <div className="relative z-10 h-full flex flex-col items-center justify-center px-4 text-center text-white">
          <span className="inline-block border border-white/60 text-[11px] font-body uppercase tracking-[0.3em] px-5 py-2 mb-5">
            Our Story
          </span>
          <h1 className="font-display text-4xl sm:text-5xl lg:text-6xl leading-tight mb-4">
            Made with 100% Love
          </h1>
          <p className="font-body text-base text-white/80 max-w-lg">
            Every bite tells a story of passion, craft, and a deep love for bringing joy through food.
          </p>
        </div>
      </section>

      {/* Story Section — text + photo */}
      <section className="max-w-6xl mx-auto px-4 py-16 lg:py-24">
        <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
          <div>
            <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">The Beginning</p>
            <h2 className="font-display text-3xl sm:text-4xl text-primary mb-6 leading-snug">
              A kitchen, a dream, and a lot of chocolate
            </h2>
            <div className="space-y-4 text-sm font-body text-gray-600 leading-relaxed">
              <p>
                Melting Moments started as a simple love for baking — late nights experimenting with
                chocolate ratios, butter temperatures, and the perfect fudgy brownie texture. What
                began as gifts for family and friends quickly became something far greater.
              </p>
              <p>
                I&apos;m Fatema Abbasi, and I founded Melting Moments from my home kitchen in the UAE
                with one mission: to create handcrafted desserts that make people smile. No factory
                lines. No bulk orders. Just me, my oven, and a genuine obsession with quality.
              </p>
              <p>
                Every batch is made fresh to order using the finest ingredients — Belgian chocolate,
                real butter, and carefully sourced produce. The result is something you can taste the
                difference in from the very first bite.
              </p>
            </div>
          </div>
          <div className="relative">
            <div className="relative aspect-[4/5] overflow-hidden">
              <Image
                src="/images/photos/person_shot_2.png"
                alt="Fatema baking in her kitchen"
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
                  src="/images/photos/person_shot_3.png"
                  alt="Freshly baked goods from Melting Moments"
                  fill
                  sizes="(max-width: 1024px) 100vw, 50vw"
                  className="object-cover object-center"
                />
              </div>
              <div className="absolute -bottom-3 -left-3 w-full aspect-[4/5] border-2 border-secondary pointer-events-none" />
            </div>
            <div className="order-1 lg:order-2">
              <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">The Craft</p>
              <h2 className="font-display text-3xl sm:text-4xl text-primary mb-6 leading-snug">
                Baked fresh, delivered with care
              </h2>
              <div className="space-y-4 text-sm font-body text-gray-600 leading-relaxed">
                <p>
                  Everything at Melting Moments is made to order. That means when you place an order,
                  we start baking — not pulling from a shelf. Your brownies are warm from the oven.
                  Your cookies are soft and chewy. Your cookie melts are perfectly gooey.
                </p>
                <p>
                  We deliver across the UAE — Dubai, Sharjah, Ajman, and more — with each order
                  packaged carefully to arrive as beautiful as it left the kitchen.
                </p>
                <p>
                  Beyond everyday treats, we love creating custom orders for special moments: birthday
                  boxes, wedding favours, Eid gifting, corporate packages. If you can dream it, we
                  can bake it.
                </p>
              </div>
              <Link
                href="/contact"
                className="inline-flex items-center gap-2 mt-6 text-xs font-body uppercase tracking-widest text-primary border border-primary px-5 py-2.5 hover:bg-primary hover:text-white transition-all duration-200"
              >
                Get in Touch
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Values */}
      <section className="max-w-6xl mx-auto px-4 py-16 lg:py-24">
        <div className="text-center mb-12">
          <p className="text-xs font-body uppercase tracking-widest text-secondary mb-3">Our Promise</p>
          <h2 className="font-display text-3xl sm:text-4xl text-primary">What we stand for</h2>
          <div className="h-px bg-secondary/40 max-w-xs mx-auto mt-5" />
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {VALUES.map(({ icon, title, description }) => (
            <div
              key={title}
              className="border border-gray-200 hover:border-primary p-6 text-center transition-colors group"
            >
              <span className="material-icons text-3xl text-secondary group-hover:text-primary mb-4 block transition-colors">
                {icon}
              </span>
              <h3 className="font-body text-sm font-medium uppercase tracking-widest text-gray-800 mb-2">
                {title}
              </h3>
              <p className="font-body text-xs text-gray-500 leading-relaxed">{description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA Banner */}
      <section className="relative overflow-hidden">
        <Image
          src="/images/photos/person_shot_4.png"
          alt="Melting Moments treats"
          fill
          sizes="100vw"
          className="object-cover object-center"
        />
        <div className="absolute inset-0 bg-primary/75" />
        <div className="relative z-10 py-20 px-4 text-center text-white">
          <h2 className="font-display text-3xl sm:text-4xl mb-4">Ready to indulge?</h2>
          <p className="font-body text-sm text-white/80 mb-8 max-w-md mx-auto">
            Browse our range of handcrafted brownies, cookies, and desserts — made fresh for you.
          </p>
          <Link
            href="/brownies"
            className="inline-flex items-center gap-2 font-body text-xs uppercase tracking-widest bg-white text-primary px-8 py-3.5 hover:bg-white/90 transition-all duration-200"
          >
            Shop Now
            <span className="material-icons text-[16px]">arrow_forward</span>
          </Link>
        </div>
      </section>
    </>
  );
}
