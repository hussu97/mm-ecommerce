'use client';

import type { CSSProperties } from 'react';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { useLocation } from '@/lib/location/LocationProvider';
import type { DeliverySpeed } from '@/lib/location/types';

export interface UspItem {
  /** A Material Icons ligature (`local_shipping`) or any emoji / short glyph. */
  icon?: string;
  label?: string;
}

export interface UspContent {
  items?: UspItem[];
  /** Seconds for one full pass. Lower is faster. */
  speed_s?: number;
  theme?: 'plum' | 'cream';
}

const MATERIAL_LIGATURE = /^[a-z][a-z0-9_]*$/;

/** Runs of the list in the track. Must match the -25% travel in `mm-marquee`. */
const COPIES = 4;

const THEMES = {
  plum: 'bg-primary text-white/95 border-y border-primary',
  cream: 'bg-[#f4ece4] text-primary border-y border-secondary/40',
} as const;

/** How fast, as three promises rather than one hedged average. */
const SPEED_KEY: Record<DeliverySpeed, string> = {
  express: 'usp.speed_express',
  same_day: 'usp.speed_same_day',
  next_day: 'usp.speed_next_day',
};

const SPEED_ICON: Record<DeliverySpeed, string> = {
  express: 'bolt',
  same_day: 'schedule',
  next_day: 'local_shipping',
};

function Item({ icon, label }: UspItem) {
  return (
    // The track is pinned to LTR (see globals.css); `dir="auto"` lets each label
    // resolve its own direction so Arabic still reads right to left.
    <span dir="auto" className="inline-flex items-center gap-2.5 px-6 sm:px-9 shrink-0">
      {icon &&
        (MATERIAL_LIGATURE.test(icon) ? (
          <span className="material-icons text-[17px] opacity-80">{icon}</span>
        ) : (
          <span className="text-base leading-none">{icon}</span>
        ))}
      <span className="font-body text-[10px] sm:text-[11px] uppercase tracking-[0.28em] whitespace-nowrap">
        {label}
      </span>
    </span>
  );
}

/**
 * The always-scrolling reassurance strip under the hero — delivery promise,
 * where it is baked, what it is made of. It replaces a paragraph of body copy
 * with something the eye picks up in half a second.
 *
 * The first item is the one the CMS cannot write: how fast we reach *this*
 * customer. It only appears once there is a real location behind it — a bare
 * "delivered in about an hour", read by somebody in Abu Dhabi whose location we
 * are guessing at from the shop's own coordinates, is exactly the promise this
 * codebase is careful not to make. See `lib/location/types.ts`.
 */
export function UspMarquee({ c }: { c: UspContent }) {
  const { t } = useTranslation();
  const { area, isKnown } = useLocation();

  const speedKey = area && isKnown ? SPEED_KEY[area.speed] : undefined;
  const items: UspItem[] = [
    ...(speedKey && area ? [{ icon: SPEED_ICON[area.speed], label: t(speedKey) }] : []),
    ...(c.items ?? []).filter(i => i.label),
  ];
  if (items.length === 0) return null;

  const style = { '--mm-marquee-duration': `${c.speed_s ?? 38}s` } as CSSProperties;

  return (
    <section
      aria-label="Why Melting Moments"
      className={`mm-marquee overflow-hidden py-3.5 sm:py-4 ${THEMES[c.theme ?? 'plum']}`}
    >
      <div className="mm-marquee-track" style={style}>
        {/* Identical runs of the same list. The track travels exactly one run's
            width (see `mm-marquee` in globals.css), so the loop restarts on a
            frame indistinguishable from the one before it. Only the first run
            is read out; the rest are decoration. */}
        {Array.from({ length: COPIES }, (_, copy) => (
          <div key={copy} className="mm-marquee-run" aria-hidden={copy > 0 || undefined}>
            {items.map((item, i) => (
              <Item key={i} {...item} />
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
