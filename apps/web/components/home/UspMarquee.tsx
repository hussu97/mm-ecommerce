import type { CSSProperties } from 'react';

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
 */
export function UspMarquee({ c }: { c: UspContent }) {
  const items = (c.items ?? []).filter(i => i.label);
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
