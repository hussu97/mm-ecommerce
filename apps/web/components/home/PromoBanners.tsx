import Link from 'next/link';
import { Reveal } from './Reveal';

export interface PromoItem {
  image?: string;
  image_mobile?: string;
  eyebrow?: string;
  title?: string;
  body?: string;
  cta_text?: string;
  cta_href?: string;
}

export interface PromosContent {
  items?: PromoItem[];
}

/**
 * Wide editorial bands between the product rails — one photograph, four words
 * and a link. These are what the old wall-of-paragraph sections became.
 */
export function PromoBanners({ c, locale }: { c: PromosContent; locale: string }) {
  const items = (c.items ?? []).filter(p => p.image || p.image_mobile);
  if (items.length === 0) return null;

  return (
    <>
      {items.map((promo, i) => {
        const wide = promo.image ?? promo.image_mobile!;
        const tall = promo.image_mobile ?? wide;

        return (
          <section
            key={`${wide}-${i}`}
            aria-label={promo.title ?? 'Promotion'}
            className="bg-[#f4ece4]"
          >
            <Reveal className="relative w-full overflow-hidden aspect-[4/3] sm:aspect-[21/8] lg:aspect-[12/5] max-h-[70vh]">
              <picture>
                <source media="(max-width: 639px)" srcSet={tall} />
                <img
                  src={wide}
                  alt=""
                  aria-hidden="true"
                  loading="lazy"
                  decoding="async"
                  className="absolute inset-0 h-full w-full object-cover transition-transform duration-[1200ms] ease-out hover:scale-[1.04]"
                />
              </picture>

              <div className="absolute inset-0 bg-gradient-to-b from-[#f6efe7]/90 via-[#f6efe7]/20 to-transparent sm:bg-gradient-to-r sm:from-[#f6efe7]/90 sm:via-[#f6efe7]/40 sm:to-transparent" />

              <div className="absolute inset-0 flex items-start sm:items-center">
                <div className="w-full max-w-7xl mx-auto px-5 sm:px-6 lg:px-8 pt-7 sm:pt-0">
                  {/* Left in both scripts — the photograph's clear side. */}
                  <div className="max-w-[18rem] sm:max-w-sm lg:max-w-md ml-0 mr-auto text-start">
                    {promo.eyebrow && (
                      <p className="text-[10px] sm:text-xs font-body uppercase tracking-[0.32em] text-primary/80 mb-2.5">
                        {promo.eyebrow}
                      </p>
                    )}
                    {promo.title && (
                      <h2 className="font-display text-2xl sm:text-4xl lg:text-5xl leading-[1.1] text-gray-800">
                        {promo.title}
                      </h2>
                    )}
                    {promo.body && (
                      <p className="hidden sm:block font-body text-sm text-gray-600 leading-relaxed mt-3.5 max-w-xs">
                        {promo.body}
                      </p>
                    )}
                    {promo.cta_text && (
                      <Link
                        href={`/${locale}${promo.cta_href ?? '/all-products'}`}
                        className="mm-sheen inline-flex items-center gap-2 mt-5 sm:mt-6 px-6 py-3 bg-primary text-white text-[11px] sm:text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
                      >
                        {promo.cta_text}
                        <span className="material-icons text-[15px] rtl:rotate-180">arrow_forward</span>
                      </Link>
                    )}
                  </div>
                </div>
              </div>
            </Reveal>
          </section>
        );
      })}
    </>
  );
}
