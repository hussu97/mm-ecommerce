'use client';

import { analytics } from '@/lib/analytics';

type Channel = 'whatsapp' | 'email' | 'instagram' | 'map';

export function ContactLink({
  href,
  channel,
  children,
  className,
  target,
  rel,
}: {
  href: string;
  channel: Channel;
  children: React.ReactNode;
  className?: string;
  target?: string;
  rel?: string;
}) {
  return (
    <a
      href={href}
      target={target}
      rel={rel}
      className={className}
      onClick={() => analytics.contactClick({ channel })}
    >
      {children}
    </a>
  );
}
