/**
 * A promo code, rendered as a thing you type rather than as a word in a
 * sentence.
 *
 * The banner and the basket tray both used to print "Use code NEW" as one run
 * of uppercase, letter-spaced text — so the one part of the sentence the
 * customer has to *do something with* looked exactly like the four words around
 * it explaining the offer. A coupon code is an instruction, and it has a
 * conventional shape: boxed, dashed, set apart. Borrowing that shape is most of
 * what makes it read as one.
 *
 * `tone` rather than a `className` passthrough, because there are exactly two
 * backgrounds this ever sits on and both need a different border and fill to
 * stay legible. A caller free to pass any classes is a caller free to put
 * white-on-white in the announcement bar.
 *
 * `select-all` so a tap or a double-click takes the whole code and nothing
 * else — people copy these rather than retype them, and a half-selected code
 * pasted into the basket is a refusal the customer cannot explain.
 */
export function CouponCode({
  code,
  tone = 'light',
}: {
  code: string;
  /** `dark` for the primary-filled announcement bar; `light` for the tray. */
  tone?: 'dark' | 'light';
}) {
  const skin =
    tone === 'dark'
      ? 'border-white/55 bg-white/15 text-white'
      : 'border-secondary bg-white text-primary';
  return (
    <span
      className={`inline-block select-all whitespace-nowrap rounded-[3px] border border-dashed px-1.5 py-[2px] font-body text-[0.95em] font-semibold leading-none tracking-[0.12em] ${skin}`}
    >
      {code}
    </span>
  );
}
