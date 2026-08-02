'use client';

import { useEffect, useRef, useState, type CSSProperties, type ElementType, type ReactNode } from 'react';

/**
 * Fades a block up into place the first time it scrolls into view.
 *
 * Deliberately not a library: the homepage is server-rendered and one shared
 * IntersectionObserver per element is cheaper than shipping a motion runtime to
 * every visitor on a 3G connection in Sharjah.
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
  const [shown, setShown] = useState<'true' | 'false' | 'skip'>('false');

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    if (typeof IntersectionObserver === 'undefined') {
      setShown('skip');
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown('true');
          if (once) observer.disconnect();
        } else if (!once) {
          setShown('false');
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
      data-shown={shown}
      className={`mm-reveal ${className}`}
      style={{ '--mm-delay': `${delay}ms` } as CSSProperties}
    >
      {children}
    </Tag>
  );
}
