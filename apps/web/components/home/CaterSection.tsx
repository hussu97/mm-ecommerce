import Link from 'next/link';
import { Reveal } from './Reveal';
import { Icon } from '@/components/ui/Icon';

export interface Occasion {
  icon: string;
  label: string;
}

export interface CaterContent {
  title?: string;
  subtitle?: string;
  occasions?: Occasion[];
  cta_text?: string;
  cta_href?: string;
}

export function CaterSection({ c, locale }: { c: CaterContent; locale: string }) {
  const occasions = c.occasions ?? [];
  if (occasions.length === 0) return null;

  return (
    <section aria-label="Occasions we cater to" className="py-14 sm:py-20 bg-[#f4ece4]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6">

        <Reveal className="text-center mb-9 sm:mb-11">
          {c.title && (
            <h2 className="font-display text-2xl sm:text-3xl text-gray-800 uppercase tracking-[0.18em]">
              {c.title}
            </h2>
          )}
          {c.subtitle && (
            <p className="font-body text-sm text-gray-500 mt-2.5">{c.subtitle}</p>
          )}
        </Reveal>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4">
          {occasions.map(({ icon, label }, i) => (
            <Reveal key={`${label}-${i}`} delay={Math.min(i, 6) * 55}>
              <div className="group h-full flex flex-col items-center justify-center gap-3 p-5 sm:p-6 bg-white border border-secondary/40 hover:border-primary hover:shadow-[0_10px_30px_-18px_rgba(138,90,100,0.55)] hover:-translate-y-0.5 transition-all duration-300">
                <span
                  className="text-3xl transition-transform duration-300 group-hover:scale-110"
                  role="img"
                  aria-label={label}
                >
                  {icon}
                </span>
                <span className="font-body text-[10px] sm:text-xs uppercase tracking-[0.18em] text-gray-600 text-center">
                  {label}
                </span>
              </div>
            </Reveal>
          ))}
        </div>

        {c.cta_text && (
          <Reveal delay={120} className="mt-9 text-center">
            <Link
              href={`/${locale}${c.cta_href ?? '/contact'}`}
              className="mm-sheen inline-flex items-center gap-2 px-7 py-3.5 bg-primary text-white text-[11px] sm:text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
            >
              {c.cta_text}
              <Icon name="arrow_forward" className="text-[15px] rtl:rotate-180" />
            </Link>
          </Reveal>
        )}

      </div>
    </section>
  );
}
