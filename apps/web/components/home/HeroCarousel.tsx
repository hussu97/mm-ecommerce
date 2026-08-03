'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { BannerPicture } from './BannerPicture';

export interface HeroSlide {
  /** Wide banner, ~12:5. Falls back to `image_mobile` if omitted. */
  image?: string;
  /** Portrait banner, ~4:5, used under the `sm` breakpoint. */
  image_mobile?: string;
  eyebrow?: string;
  headline?: string;
  /** Second line of the headline, set in the brand plum. */
  highlight?: string;
  body?: string;
  cta_text?: string;
  cta_href?: string;
  secondary_text?: string;
  secondary_href?: string;
}

export interface HeroContent {
  slides?: HeroSlide[];
  /** Milliseconds per slide. 0 (or a single slide) disables auto-advance. */
  autoplay_ms?: number;

  /* ── legacy single-hero fields, still honoured so an un-migrated CMS row
        keeps rendering something sensible ─────────────────────────────────── */
  tagline?: string;
  headline?: string;
  shop_button_text?: string;
  shop_button_href?: string;
  story_button_text?: string;
  story_button_href?: string;
}

const DEFAULT_AUTOPLAY = 6000;

/** The banner artwork that ships with the repo, used when the CMS has no slides. */
const FALLBACK_SLIDES: HeroSlide[] = [
  {
    image: '/images/banners/hero-cookie-melt.jpg',
    image_mobile: '/images/banners/hero-cookie-melt-mobile.jpg',
    cta_href: '/all-products',
  },
];

function slidesFrom(c: HeroContent): HeroSlide[] {
  if (c.slides?.length) return c.slides;

  // Legacy shape: one slide built out of the flat hero fields.
  if (c.headline || c.tagline) {
    return [
      {
        ...FALLBACK_SLIDES[0],
        eyebrow: c.tagline,
        headline: c.headline,
        cta_text: c.shop_button_text,
        cta_href: c.shop_button_href ?? '/all-products',
        secondary_text: c.story_button_text,
        secondary_href: c.story_button_href,
      },
    ];
  }

  return FALLBACK_SLIDES;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  );
}

export function HeroCarousel({ c, locale }: { c: HeroContent; locale: string }) {
  const slides = slidesFrom(c);
  const count = slides.length;
  const interval = c.autoplay_ms ?? DEFAULT_AUTOPLAY;

  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  const touchStart = useRef<number | null>(null);
  const current = slides[active] ?? slides[0];

  const go = useCallback(
    (next: number) => setActive(((next % count) + count) % count),
    [count],
  );

  useEffect(() => {
    if (count < 2 || paused || interval <= 0 || prefersReducedMotion()) return;
    const id = setTimeout(() => go(active + 1), interval);
    return () => clearTimeout(id);
  }, [active, count, paused, interval, go]);

  function onTouchStart(e: React.TouchEvent) {
    touchStart.current = e.touches[0].clientX;
  }

  function onTouchEnd(e: React.TouchEvent) {
    const start = touchStart.current;
    touchStart.current = null;
    if (start === null) return;
    const dx = e.changedTouches[0].clientX - start;
    if (Math.abs(dx) > 45) go(active + (dx < 0 ? 1 : -1));
  }

  return (
    <section
      aria-label="Featured"
      aria-roledescription="carousel"
      className="relative bg-[#f4ece4] overflow-hidden"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      <div className="relative w-full aspect-[4/5] sm:aspect-[16/7] lg:aspect-[12/5] max-h-[86vh] sm:max-h-[80vh]">
        {/* Photographs cross-fade. The scrim and the copy do not — they are
            rendered once, below, for whichever slide is current. Fading whole
            slides on top of each other meant two headlines legible at the same
            time for the length of the transition, and two scrims stacking into
            a wash that greyed the picture out under them. */}
        {slides.map((slide, i) => {
          const wide = slide.image ?? slide.image_mobile ?? FALLBACK_SLIDES[0].image!;
          const tall = slide.image_mobile ?? wide;
          const isActive = i === active;

          return (
            <div
              key={`${wide}-${i}`}
              role="group"
              aria-roledescription="slide"
              aria-label={`${i + 1} of ${count}`}
              aria-hidden={!isActive}
              className={`absolute inset-0 transition-opacity duration-[900ms] ease-out ${
                isActive ? 'opacity-100' : 'opacity-0'
              }`}
            >
              {/* <picture> rather than next/image: these are pre-graded, correctly
                  sized JPEGs and the mobile frame is a different crop, which is
                  art direction the image component cannot express. Nothing
                  re-encodes them for us either, so BannerPicture offers the AVIF
                  and WebP built next to them. */}
              <BannerPicture
                wide={wide}
                tall={tall}
                decoding={i === 0 ? 'sync' : 'async'}
                // Every slide is inside the viewport, so `lazy` buys nothing
                // but a blank frame the first time the carousel advances past
                // an image that has not arrived yet. Fetch them all, and let
                // priority keep the later ones behind the LCP frame.
                loading="eager"
                fetchPriority={i === 0 ? 'high' : 'low'}
                className={`absolute inset-0 h-full w-full object-cover ${
                  isActive ? 'mm-kenburns' : ''
                }`}
              />
            </div>
          );
        })}

        {/* Scrim: the artwork leaves its negative space top-left, so the copy
            sits there in both scripts and the wash follows it. */}
        <div className="absolute inset-0 pointer-events-none bg-gradient-to-b from-[#f6efe7]/92 via-[#f6efe7]/25 to-transparent sm:bg-gradient-to-r sm:from-[#f6efe7]/92 sm:via-[#f6efe7]/45 sm:to-transparent" />

        <div className="absolute inset-0 flex items-start sm:items-center pointer-events-none">
          <div className="w-full max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 pt-8 sm:pt-0">
            {/* Physically left in both scripts: the artwork puts the product on
                the right and leaves this side clear, so the copy block cannot
                follow the reading direction. The text inside it still aligns to
                the script's own start. */}
            <div className="max-w-[19rem] sm:max-w-md lg:max-w-lg ml-0 mr-auto text-start pointer-events-auto">
              {current.eyebrow && (
                <p
                  key={`e${active}`}
                  className="mm-slide-in text-[10px] sm:text-xs font-body uppercase tracking-[0.32em] text-primary/80 mb-3"
                >
                  {current.eyebrow}
                </p>
              )}

              {(current.headline || current.highlight) && (
                <h1
                  key={`h${active}`}
                  className="mm-slide-in font-display text-[2rem] leading-[1.08] sm:text-5xl lg:text-6xl text-gray-800"
                  style={{ ['--mm-delay' as string]: '90ms' }}
                >
                  {current.headline}
                  {current.highlight && (
                    <>
                      {current.headline ? <br /> : null}
                      <span className="text-primary">{current.highlight}</span>
                    </>
                  )}
                </h1>
              )}

              {current.body && (
                <p
                  key={`b${active}`}
                  className="mm-slide-in hidden sm:block font-body text-gray-600 text-sm leading-relaxed mt-4 max-w-sm"
                  style={{ ['--mm-delay' as string]: '160ms' }}
                >
                  {current.body}
                </p>
              )}

              <div
                key={`c${active}`}
                className="mm-slide-in flex flex-wrap gap-2.5 mt-5 sm:mt-7"
                style={{ ['--mm-delay' as string]: '220ms' }}
              >
                {current.cta_text && (
                  <Link
                    href={`/${locale}${current.cta_href ?? '/all-products'}`}
                    className="mm-sheen inline-flex items-center gap-2 px-6 sm:px-7 py-3 sm:py-3.5 bg-primary text-white text-[11px] sm:text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
                  >
                    {current.cta_text}
                    <span className="material-icons text-[15px] rtl:rotate-180">arrow_forward</span>
                  </Link>
                )}
                {current.secondary_text && (
                  <Link
                    href={`/${locale}${current.secondary_href ?? '/about'}`}
                    className="inline-flex items-center px-6 sm:px-7 py-3 sm:py-3.5 border border-primary/70 text-primary text-[11px] sm:text-xs font-body uppercase tracking-widest hover:bg-primary hover:text-white transition-colors duration-200"
                  >
                    {current.secondary_text}
                  </Link>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Controls live together along the bottom edge. Arrows parked at the
            vertical midpoint sat straight on top of the headline. */}
        {count > 1 && (
          <div className="absolute bottom-4 sm:bottom-6 inset-x-0 z-10 px-5 sm:px-6 lg:px-8 flex items-center justify-center">
            <div className="w-full max-w-7xl mx-auto flex items-center justify-center sm:justify-between">
              <div className="flex gap-2">
                {slides.map((_, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => go(i)}
                    aria-label={`Go to slide ${i + 1}`}
                    aria-current={i === active}
                    className={`h-1.5 rounded-full transition-all duration-300 ${
                      i === active ? 'w-7 bg-primary' : 'w-1.5 bg-primary/35 hover:bg-primary/60'
                    }`}
                  />
                ))}
              </div>

              <div className="hidden sm:flex gap-2">
                <button
                  type="button"
                  onClick={() => go(active - 1)}
                  aria-label="Previous slide"
                  className="h-10 w-10 flex items-center justify-center rounded-full bg-white/75 backdrop-blur-sm text-primary hover:bg-white transition-colors"
                >
                  <span className="material-icons text-xl rtl:rotate-180">chevron_left</span>
                </button>
                <button
                  type="button"
                  onClick={() => go(active + 1)}
                  aria-label="Next slide"
                  className="h-10 w-10 flex items-center justify-center rounded-full bg-white/75 backdrop-blur-sm text-primary hover:bg-white transition-colors"
                >
                  <span className="material-icons text-xl rtl:rotate-180">chevron_right</span>
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
