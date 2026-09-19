'use client';

import { useCallback, useEffect, useState } from 'react';
import { Icon } from '@/components/ui/Icon';
import type { CaterGalleryItem } from './CaterSection';

/**
 * The custom cakes Melting Moments makes, as an auto-scrolling strip you can tap
 * to see full size.
 *
 * The strip is a CSS marquee: the list is rendered twice back-to-back and the
 * track is translated by exactly half its width, so the second copy is where the
 * first began and the loop is seamless. It pauses on hover and — because motion
 * that cannot be stopped is a genuine accessibility problem — turns into an
 * ordinary horizontal scroll for anyone who asked their system for reduced
 * motion. Tapping an image opens a lightbox: the photo at full scale over a
 * dimmed page, dismissed with the X, a backdrop tap, or Escape.
 */
export function CaterGallery({ items }: { items: CaterGalleryItem[] }) {
  const [active, setActive] = useState<CaterGalleryItem | null>(null);

  const shown = items.filter((it) => it.image);

  const close = useCallback(() => setActive(null), []);

  // Escape closes, and while the lightbox is open the page behind it must not
  // scroll under the finger.
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [active, close]);

  if (shown.length === 0) return null;

  // Duplicated once for the seamless loop. Enough copies that even a short list
  // fills the track and the wrap is never visible.
  const loop = shown.length < 4 ? [...shown, ...shown, ...shown, ...shown] : [...shown, ...shown];

  return (
    <>
      <div className="mm-cater-gallery group relative overflow-hidden">
        {/* Soft fades at both edges so images slide in and out rather than being
            chopped off at the container border. */}
        <div className="pointer-events-none absolute inset-y-0 start-0 z-10 w-10 sm:w-16 bg-gradient-to-r from-[#f4ece4] to-transparent" />
        <div className="pointer-events-none absolute inset-y-0 end-0 z-10 w-10 sm:w-16 bg-gradient-to-l from-[#f4ece4] to-transparent" />

        <div className="mm-cater-track flex gap-4 w-max">
          {loop.map((item, i) => (
            <button
              key={`${item.image}-${i}`}
              type="button"
              onClick={() => setActive(item)}
              aria-label={item.alt ? `View ${item.alt}` : 'View cake'}
              className="relative shrink-0 h-52 w-44 sm:h-64 sm:w-56 overflow-hidden rounded-sm bg-white/60 border border-secondary/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={item.image}
                alt={item.alt ?? ''}
                loading="lazy"
                className="h-full w-full object-cover transition-transform duration-500 hover:scale-105"
              />
              {item.alt && (
                <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/55 to-transparent px-3 pb-2 pt-6 text-start font-body text-[10px] uppercase tracking-[0.16em] text-white">
                  {item.alt}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {active && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={active.alt ?? 'Cake photo'}
          onClick={close}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-4 sm:p-8"
        >
          <button
            type="button"
            onClick={close}
            aria-label="Close"
            className="absolute top-4 end-4 flex h-11 w-11 items-center justify-center rounded-full bg-white/15 text-white hover:bg-white/25 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-white"
          >
            <Icon name="close" className="text-[24px]" />
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={active.image}
            alt={active.alt ?? ''}
            onClick={(e) => e.stopPropagation()}
            className="max-h-full max-w-full object-contain rounded-sm shadow-2xl"
          />
        </div>
      )}

      <style>{`
        .mm-cater-track {
          animation: mm-cater-scroll 40s linear infinite;
        }
        .mm-cater-gallery:hover .mm-cater-track {
          animation-play-state: paused;
        }
        @keyframes mm-cater-scroll {
          from { transform: translateX(0); }
          to { transform: translateX(-50%); }
        }
        /* RTL slides the other way so cakes still enter from the leading edge. */
        [dir='rtl'] .mm-cater-track {
          animation-name: mm-cater-scroll-rtl;
        }
        @keyframes mm-cater-scroll-rtl {
          from { transform: translateX(0); }
          to { transform: translateX(50%); }
        }
        @media (prefers-reduced-motion: reduce) {
          .mm-cater-gallery { overflow-x: auto; }
          .mm-cater-track { animation: none; }
        }
      `}</style>
    </>
  );
}
