'use client';

import { analytics, type Surface } from '@/lib/analytics';

type Channel = 'whatsapp' | 'email' | 'instagram' | 'map' | 'phone';

export function ContactLink({
  href,
  channel,
  children,
  className,
  target,
  rel,
  surface = 'contact',
  ariaLabel,
}: {
  href: string;
  channel: Channel;
  children: React.ReactNode;
  className?: string;
  target?: string;
  rel?: string;
  /**
   * Where the link is.
   *
   * Only the contact page used to be wired up, so the WhatsApp buttons in the
   * footer and at the bottom of the FAQ — the two a customer reaches when the
   * site has already failed to answer them — counted for nothing. Reaching for
   * WhatsApp from the FAQ is a very different signal from reaching for it from
   * the contact page, and until now they were the same number: zero.
   */
  surface?: Surface;
  ariaLabel?: string;
}) {
  return (
    <a
      href={href}
      target={target}
      rel={rel}
      className={className}
      aria-label={ariaLabel}
      onClick={() => analytics.contactClick({ channel, surface })}
    >
      {children}
    </a>
  );
}
