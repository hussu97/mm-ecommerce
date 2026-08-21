import { modernSources } from '@/lib/images';

interface BannerPictureProps {
  /** Wide frame, shown from `sm` up. */
  wide: string;
  /** Portrait frame, shown below `sm`. Pass the same value to disable the swap. */
  tall: string;
  /**
   * What the photograph shows. Required, and required as a decision: pass `''`
   * only for a band that genuinely carries no information the surrounding copy
   * does not already give.
   */
  alt: string;
  className?: string;
  loading?: 'eager' | 'lazy';
  decoding?: 'sync' | 'async' | 'auto';
  fetchPriority?: 'high' | 'low' | 'auto';
}

/**
 * The two art-directed bands — the hero and the promo strips — share this.
 *
 * `<source>` candidates are evaluated in order and the first one whose `media`
 * and `type` both match is taken, so the narrow-viewport entries have to come
 * first as a block, most-preferred format at the top. The plain `<img>` at the
 * end is the wide JPEG, which is what a browser that understood none of the
 * above ends up with.
 *
 * `alt` used to be hard-coded to `''` with `aria-hidden`, which said these
 * photographs carry nothing — and they are the product, shot for this shop, on
 * a page whose only other content is four words of overlay copy. Bing reported
 * the home page for it and a screen reader got a hero band that was not there.
 * The caller decides now; `aria-hidden` follows the decision rather than
 * pre-empting it, so an empty string still means decorative and still hides the
 * element from the tree.
 */
export function BannerPicture({
  wide,
  tall,
  alt,
  className,
  loading = 'lazy',
  decoding = 'async',
  fetchPriority,
}: BannerPictureProps) {
  const tallModern = modernSources(tall);
  const wideModern = modernSources(wide);
  const swaps = tall !== wide;

  return (
    <picture>
      {swaps && tallModern && (
        <>
          <source media="(max-width: 639px)" type="image/avif" srcSet={tallModern.avif} />
          <source media="(max-width: 639px)" type="image/webp" srcSet={tallModern.webp} />
        </>
      )}
      {swaps && <source media="(max-width: 639px)" srcSet={tall} />}

      {wideModern && (
        <>
          <source type="image/avif" srcSet={wideModern.avif} />
          <source type="image/webp" srcSet={wideModern.webp} />
        </>
      )}

      <img
        src={wide}
        alt={alt}
        aria-hidden={alt === '' ? 'true' : undefined}
        loading={loading}
        decoding={decoding}
        fetchPriority={fetchPriority}
        className={className}
      />
    </picture>
  );
}
