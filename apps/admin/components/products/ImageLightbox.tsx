'use client';

import { useEffect } from 'react';
import Image from 'next/image';

interface Props {
  urls: string[];
  /** The image showing, or null when the lightbox is closed. */
  index: number | null;
  onIndexChange: (index: number | null) => void;
}

/**
 * Full-size view of a product's images. The form only shows 96px thumbnails,
 * which is too small to judge a photo by — this is where you check one before
 * deciding to keep, reorder or remove it.
 *
 * Esc or a click on the backdrop closes it; the arrow keys step through.
 */
export function ImageLightbox({ urls, index, onIndexChange }: Props) {
  useEffect(() => {
    if (index === null) return;
    const current = index;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onIndexChange(null);
      else if (e.key === 'ArrowLeft' && current > 0) onIndexChange(current - 1);
      else if (e.key === 'ArrowRight' && current < urls.length - 1) onIndexChange(current + 1);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [index, urls.length, onIndexChange]);

  if (index === null || index < 0 || index >= urls.length) return null;
  const url = urls[index];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Image preview"
      className="fixed inset-0 z-50 flex flex-col bg-black/85"
      onClick={() => onIndexChange(null)}
    >
      <div className="flex items-center justify-between px-4 py-3 text-white text-sm font-body">
        <span>
          {index + 1} / {urls.length}
          {index === 0 && <span className="ml-2 text-[10px] uppercase tracking-wide opacity-70">Cover</span>}
        </span>
        <div className="flex items-center gap-4" onClick={e => e.stopPropagation()}>
          <a href={url} target="_blank" rel="noreferrer" className="hover:underline">
            Open original
          </a>
          <button type="button" onClick={() => onIndexChange(null)} aria-label="Close preview" className="hover:opacity-70">
            <span className="material-icons text-[22px]">close</span>
          </button>
        </div>
      </div>

      <div className="relative flex-1 flex items-center justify-center px-14 pb-6">
        {index > 0 && (
          <button
            type="button"
            onClick={e => { e.stopPropagation(); onIndexChange(index - 1); }}
            aria-label="Previous image"
            className="absolute left-3 text-white hover:opacity-70"
          >
            <span className="material-icons text-[36px]">chevron_left</span>
          </button>
        )}
        <div className="relative w-full h-full" onClick={e => e.stopPropagation()}>
          <Image src={url} alt="" fill sizes="90vw" className="object-contain" />
        </div>
        {index < urls.length - 1 && (
          <button
            type="button"
            onClick={e => { e.stopPropagation(); onIndexChange(index + 1); }}
            aria-label="Next image"
            className="absolute right-3 text-white hover:opacity-70"
          >
            <span className="material-icons text-[36px]">chevron_right</span>
          </button>
        )}
      </div>
    </div>
  );
}
