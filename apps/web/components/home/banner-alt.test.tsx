/**
 * Every photograph on the home page says what it is.
 *
 * `BannerPicture` hard-coded `alt=""` and `aria-hidden`, and both callers — the
 * hero carousel and the promo bands — are the largest images on the site. Bing's
 * site scan reported `/en` for a missing alt attribute; a screen reader got
 * nothing at all where the product is.
 *
 * The trap this guards is that `alt=""` is *valid*, so nothing in the build,
 * the types or the linter objects to it. It has to be asserted from the
 * rendered output, on the components that actually render the banners, or the
 * next person to add a caller reintroduces it by copying a neighbour.
 */

import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

import { HeroCarousel, type HeroContent } from './HeroCarousel';
import { MeetTheBaker, type BakerContent } from './MeetTheBaker';
import { PromoBanners, type PromosContent } from './PromoBanners';
import type { Category } from '@/lib/types';

vi.mock('@/lib/i18n/TranslationProvider', () => ({
  useTranslation: () => ({
    locale: 'en',
    direction: 'ltr' as const,
    translations: {},
    t: (key: string) => key,
  }),
}));

const CATEGORIES = [
  { id: '1', slug: 'cat-brownies', name: 'Brownies', is_active: true },
] as unknown as Category[];

function altsOf(container: HTMLElement): (string | null)[] {
  return [...container.querySelectorAll('img')].map((img) => img.getAttribute('alt'));
}

describe('home page banner alt text', () => {
  it('describes a hero slide with its own alt when the CMS supplies one', () => {
    const c: HeroContent = {
      slides: [
        {
          image: '/images/banners/hero-brownies.jpg',
          image_alt: 'A tray of fudgy brownies, freshly cut',
          headline: 'Brownies worth',
          highlight: 'the detour',
        },
      ],
    };
    const { container } = render(<HeroCarousel c={c} locale="en" categories={CATEGORIES} />);
    expect(altsOf(container)).toContain('A tray of fudgy brownies, freshly cut');
  });

  it('falls back to the slide headline, which was written about the picture', () => {
    const c: HeroContent = {
      slides: [
        {
          image: '/images/banners/hero-brownies.jpg',
          headline: 'Brownies worth',
          highlight: 'the detour',
        },
      ],
    };
    const { container } = render(<HeroCarousel c={c} locale="en" categories={CATEGORIES} />);
    expect(altsOf(container)).toContain('Brownies worth the detour');
  });

  it('still names the shop for a slide carrying no copy at all', () => {
    // The seeded fallback slide is exactly this: an image and a link, nothing
    // to borrow a description from. It is still a photograph of the product.
    const c: HeroContent = { slides: [{ image: '/images/banners/hero-mix-box.jpg' }] };
    const { container } = render(<HeroCarousel c={c} locale="en" categories={CATEGORIES} />);
    for (const alt of altsOf(container)) {
      expect(alt).toBeTruthy();
    }
  });

  it('describes a promo band with its title', () => {
    const c: PromosContent = {
      items: [
        {
          image: '/images/banners/promo.jpg',
          title: 'Straight from the fridge',
          cta_href: '/cat-brownies',
        },
      ],
    };
    const { container } = render(<PromoBanners c={c} locale="en" categories={CATEGORIES} />);
    expect(altsOf(container)).toEqual(['Straight from the fridge']);
  });

  it('describes the baker backdrop and her kitchen shots', () => {
    // The backdrop sits under an 80% plum wash, which is why it was `alt=""`
    // plus `aria-hidden`. It is still the only photograph of the baker on the
    // home page, and it is what kept `/en` reported after the banners were
    // fixed. The gallery's `?? ''` had the same shape: a CMS row with no alt
    // written yet produced an unlabelled kitchen shot.
    const c: BakerContent = {
      quote: 'Baked the morning it goes out',
      gallery: [
        { image: '/images/photos/kitchen-1.jpg', alt: 'Cookies cooling on a rack' },
        { image: '/images/photos/kitchen-2.jpg' },
      ],
    };
    const { container } = render(<MeetTheBaker c={c} locale="en" />);
    const alts = altsOf(container);
    expect(alts).toContain('Cookies cooling on a rack');
    expect(alts.length).toBe(3);
    for (const alt of alts) {
      expect(alt).toBeTruthy();
    }
  });

  it('never leaves a banner with an empty alt', () => {
    const hero: HeroContent = {
      slides: [{ image: '/images/banners/a.jpg' }, { image: '/images/banners/b.jpg' }],
    };
    const promos: PromosContent = {
      items: [{ image: '/images/banners/c.jpg', cta_href: '/cat-brownies' }],
    };
    const { container } = render(
      <>
        <HeroCarousel c={hero} locale="en" categories={CATEGORIES} />
        <PromoBanners c={promos} locale="en" categories={CATEGORIES} />
      </>,
    );
    const alts = altsOf(container);
    expect(alts.length).toBeGreaterThan(0);
    for (const alt of alts) {
      expect(alt).not.toBeNull();
      expect(alt).not.toBe('');
    }
  });
});
