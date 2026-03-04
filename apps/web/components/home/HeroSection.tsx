import Image from 'next/image';
import Link from 'next/link';

export function HeroSection() {
  return (
    <section aria-label="Hero" className="bg-[#f9f5f0]">

      {/* 2-col: text + baker photo */}
      <div className="max-w-7xl mx-auto px-4 py-16 lg:py-24 grid lg:grid-cols-2 gap-12 items-center">

        {/* Left: Text */}
        <div className="flex flex-col gap-6 order-2 lg:order-1">
          <p className="text-xs font-body uppercase tracking-[0.3em] text-primary/70">
            Handcrafted in the UAE
          </p>
          <h1 className="font-display text-4xl sm:text-5xl lg:text-6xl leading-tight text-gray-800">
            Moments that<br />
            <em className="text-primary not-italic">melt away</em>
          </h1>
          <p className="font-body text-gray-500 text-base leading-relaxed max-w-md">
            Artisanal brownies, cookies, and desserts — lovingly baked by Fatema Abbasi.
            Every bite is a little piece of home.
          </p>
          <div className="flex flex-wrap gap-3 pt-2">
            <Link
              href="/brownies"
              className="inline-flex items-center gap-2 px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
            >
              Shop Now
            </Link>
            <Link
              href="/about"
              className="inline-flex items-center gap-2 px-6 py-3 border border-primary text-primary text-xs font-body uppercase tracking-widest hover:bg-primary hover:text-white transition-all duration-200"
            >
              Our Story
            </Link>
          </div>
        </div>

        {/* Right: Baker photo with decorative offset border */}
        <div className="relative order-1 lg:order-2">
          <div className="relative aspect-[4/5] w-full max-w-md mx-auto lg:max-w-none">
            <Image
              src="/images/photos/person_shot_1.jpg"
              alt="Fatema Abbasi — founder of Melting Moments Cakes"
              fill
              sizes="(max-width: 1024px) 90vw, 45vw"
              className="object-cover object-top"
              priority
            />
          </div>
          <div className="absolute -bottom-3 -right-3 w-full max-w-md mx-auto lg:max-w-none aspect-[4/5] border-2 border-secondary pointer-events-none" />
        </div>

      </div>

      {/* 3-col action shots */}
      <div className="max-w-7xl mx-auto px-4 pb-16 grid grid-cols-3 gap-3 sm:gap-4">
        {[
          { src: '/images/photos/person_shot_2.png', alt: 'Freshly baked brownies' },
          { src: '/images/photos/person_shot_3.png', alt: 'Handcrafted cookies' },
          { src: '/images/photos/person_shot_4.png', alt: 'Artisanal desserts' },
        ].map(({ src, alt }) => (
          <div key={src} className="relative aspect-square overflow-hidden">
            <Image
              src={src}
              alt={alt}
              fill
              sizes="(max-width: 640px) 33vw, (max-width: 1280px) 30vw, 400px"
              className="object-cover hover:scale-105 transition-transform duration-500"
            />
          </div>
        ))}
      </div>

    </section>
  );
}
