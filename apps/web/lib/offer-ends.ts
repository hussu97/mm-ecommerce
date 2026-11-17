/**
 * The last day the advertised coupon can be used, as copy: "Dec 31" /
 * "31 ديسمبر". `null` when the campaign is open-ended, so a surface says
 * nothing about an end rather than inventing one.
 *
 * Read off `valid_until`, never written into a string. The row is the single
 * place the campaign's terms live (see `lib/offer.ts`): extend it in the admin
 * and every "offer ends" moves with it; let it lapse and the offer — this line
 * included — stops being advertised at all.
 *
 * Kept out of `lib/offer.ts` because that module is server-only through its
 * fetch, and the strip and the basket tray that print this are client
 * components.
 *
 * **Which day.** `validate` refuses only once `now > valid_until`, and the
 * admin's date field saves a bare date as midnight UTC — so "valid until
 * 2027-01-01" is stored as `2027-01-01T00:00:00Z`, and the last day anyone
 * can use it is Dec 31. The day stated is therefore the UTC date of the
 * instant just before the boundary. In UAE time the code works until 04:00 the
 * morning after; stating the earlier day under-promises by those hours and
 * never over-promises, which is the side to err on for a deadline.
 */
export function offerEndDate(
  validUntil: string | null | undefined,
  locale: string,
): string | null {
  if (!validUntil) return null;
  const boundary = Date.parse(validUntil);
  if (Number.isNaN(boundary)) return null;
  const lastInstant = new Date(boundary - 1);
  // Latin digits in Arabic, as every other figure in the offer copy is
  // ("خصم 15%"), and the explicit time zone keeps the server's rendering and
  // the browser's identical.
  return locale === 'ar'
    ? new Intl.DateTimeFormat('ar-u-nu-latn', {
        day: 'numeric',
        month: 'long',
        timeZone: 'UTC',
      }).format(lastInstant)
    : new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        timeZone: 'UTC',
      }).format(lastInstant);
}
