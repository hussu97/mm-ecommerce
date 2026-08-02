import Image from 'next/image';
import Link from 'next/link';
import { Reveal } from './Reveal';

export interface BakerGalleryItem {
  image?: string;
  alt?: string;
}

export interface BakerContent {
  label?: string;
  quote?: string;
  /** One short line under the quote. Long paragraphs belong on /about. */
  body?: string;
  button_text?: string;
  button_href?: string;
  /** Backdrop photo behind the plum wash. */
  image?: string;
  /** Three-up strip of kitchen shots under the quote. */
  gallery?: BakerGalleryItem[];
}

const DEFAULT_IMAGE = '/images/photos/person_shot_1.jpg';

export function MeetTheBaker({ c, locale }: { c: BakerContent; locale: string }) {
  const gallery = (c.gallery ?? []).filter(g => g.image).slice(0, 3);

  return (
    <section aria-label="Meet the baker" className="relative overflow-hidden">
      <div className="relative">

        {/* Backdrop */}
        <div className="absolute inset-0">
          <Image
            src={c.image || DEFAULT_IMAGE}
            alt=""
            aria-hidden="true"
            fill
            sizes="100vw"
            className="object-cover object-center"
          />
          <div className="absolute inset-0 bg-primary/80" />
          {/* A touch of depth so the flat wash doesn't read as a colour block. */}
          <div className="absolute inset-0 bg-gradient-to-b from-black/25 via-transparent to-black/30" />
        </div>

        <div className="relative z-10 px-4 sm:px-6 py-16 sm:py-24">
          <div className="max-w-3xl mx-auto text-center text-white">

            {c.label && (
              <Reveal>
                <span className="inline-block border border-white/50 text-[10px] sm:text-xs font-body uppercase tracking-[0.32em] px-4 py-1.5">
                  {c.label}
                </span>
              </Reveal>
            )}

            {c.quote && (
              <Reveal delay={90}>
                <h2 className="font-display text-2xl sm:text-4xl italic leading-snug mt-6">
                  &ldquo;{c.quote}&rdquo;
                </h2>
              </Reveal>
            )}

            {c.body && (
              <Reveal delay={160}>
                <p className="font-body text-white/80 text-sm leading-relaxed mt-4 max-w-md mx-auto">
                  {c.body}
                </p>
              </Reveal>
            )}

            {c.button_text && (
              <Reveal delay={230}>
                <Link
                  href={`/${locale}${c.button_href ?? '/about'}`}
                  className="inline-flex items-center gap-2 mt-7 border border-white text-white text-[11px] sm:text-xs font-body uppercase tracking-widest px-6 py-3 hover:bg-white hover:text-primary transition-colors duration-300"
                >
                  {c.button_text}
                  <span className="material-icons text-[15px] rtl:rotate-180">arrow_forward</span>
                </Link>
              </Reveal>
            )}
          </div>

          {gallery.length > 0 && (
            <div className="max-w-4xl mx-auto mt-12 sm:mt-16 grid grid-cols-3 gap-2.5 sm:gap-4">
              {gallery.map((shot, i) => (
                <Reveal key={shot.image} delay={i * 90}>
                  <div className="group relative aspect-square overflow-hidden">
                    <Image
                      src={shot.image!}
                      alt={shot.alt ?? ''}
                      fill
                      loading="lazy"
                      sizes="(max-width: 640px) 33vw, 300px"
                      className="object-cover transition-transform duration-[900ms] ease-out group-hover:scale-110"
                    />
                    <span className="absolute inset-0 ring-1 ring-inset ring-white/25" />
                  </div>
                </Reveal>
              ))}
            </div>
          )}
        </div>

      </div>
    </section>
  );
}
