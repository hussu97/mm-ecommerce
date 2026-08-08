import { ICON_PATHS } from './icon-paths';

/**
 * An icon, drawn rather than typed.
 *
 * This replaces `<span className="material-icons">close</span>`. That worked by
 * loading the Material Icons webfont from Google and relying on a ligature to
 * turn the word "close" into a glyph, which cost:
 *
 *   - two third-party origins (`fonts.googleapis.com` for the stylesheet,
 *     `fonts.gstatic.com` for the font), each with its own DNS and TLS;
 *   - 128 KB for a font the site draws about fifty glyphs from;
 *   - and a wait, because the stylesheet was injected by an `afterInteractive`
 *     script, so every icon on the page was missing until hydration finished.
 *
 * The whole set is now ~19 KB of path data in the bundle, drawn immediately,
 * with no network request at all. The outlines are extracted from the real font
 * by `scripts/extract-icons.mjs`, not redrawn by hand, so nothing changed shape.
 *
 * Sizing and colour work exactly as they did: `1em` square means the existing
 * `text-sm` / `text-[17px]` / `text-5xl` classes still size it, and
 * `currentColor` means the existing text-colour classes still colour it. The
 * `-0.125em` baseline nudge is what the font's own metrics were doing.
 */
export function Icon({
  name,
  className,
  title,
}: {
  /**
   * A Material Icons ligature name. Unknown names render nothing rather than
   * falling back to text — the CMS lets an admin type any name into a USP row
   * (`UspMarquee`), and the one thing that must never happen is the word
   * `local_shipping` appearing on the page, which is exactly what the webfont
   * did when a name was wrong.
   */
  name: string;
  className?: string;
  /** Set only when the icon is the sole carrier of meaning. */
  title?: string;
}) {
  const d = ICON_PATHS[name];
  if (!d) return null;

  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="currentColor"
      className={className}
      style={{ display: 'inline-block', verticalAlign: '-0.125em', flexShrink: 0 }}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      {title && <title>{title}</title>}
      <path d={d} />
    </svg>
  );
}
