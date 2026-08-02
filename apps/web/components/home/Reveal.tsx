'use client';

import { useEffect, useRef, type CSSProperties, type ElementType, type ReactNode } from 'react';

/**
 * Fades a block up into place the first time it scrolls into view.
 *
 * Deliberately not a library: the homepage is server-rendered and one
 * IntersectionObserver per block is cheaper than shipping a motion runtime to
 * every visitor on a phone in Sharjah. The observer flips a `data-shown`
 * attribute directly rather than going through state — nothing about the React
 * tree changes when a section becomes visible, so there is no re-render.
 *
 * If the browser has no IntersectionObserver the block is shown immediately —
 * content must never be stranded at opacity 0. The JS-disabled case is covered
 * by the `<noscript>` override on the home page itself.
 */
export function Reveal({
  children,
  as: Tag = 'div',
  delay = 0,
  className = '',
  once = true,
}: {
  children: ReactNode;
  as?: ElementType;
  /** Stagger, in ms — use with an index to cascade a row of cards. */
  delay?: number;
  className?: string;
  once?: boolean;
}) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    if (typeof IntersectionObserver === 'undefined') {
      el.dataset.shown = 'skip';
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        // Already scrolled past — a restored scroll position or a jump to an
        // anchor must not leave everything above the fold at opacity 0.
        const scrolledPast = entry.boundingClientRect.bottom < 0;

        if (entry.isIntersecting || scrolledPast) {
          el.dataset.shown = 'true';
          if (once) observer.disconnect();
        } else if (!once) {
          el.dataset.shown = 'false';
        }
      },
      { threshold: 0.12, rootMargin: '0px 0px -8% 0px' },
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [once]);

  return (
    <Tag
      ref={ref}
      data-shown="false"
      className={`mm-reveal ${className}`}
      style={{ '--mm-delay': `${delay}ms` } as CSSProperties}
    >
      {children}
    </Tag>
  );
}
