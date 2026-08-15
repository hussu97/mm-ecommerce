export function cn(...classes: (string | undefined | null | false)[]): string {
  return classes.filter(Boolean).join(' ');
}

export function generateId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/**
 * `Intl` does the Arabic ordering; built once because the constructor is the
 * expensive part and product grids call this per card.
 *
 * `nu-latn` keeps Latin digits: prices reach the same customer in Latin
 * numerals everywhere else — SMS, card statements, receipts — and a figure
 * that changes script between the shelf and the bill invites doubt about
 * whether it changed value. The currency stays the "AED" *code* rather than a
 * localized symbol for the same reason. Grouping is off to match the English
 * shape below. What Intl is actually here for is placement: it inserts the
 * RTL mark so "12.00 AED" sits correctly inside an Arabic sentence instead of
 * splitting it left-to-right.
 */
const AR_PRICE = new Intl.NumberFormat('ar-AE-u-nu-latn', {
  style: 'currency',
  currency: 'AED',
  currencyDisplay: 'code',
  useGrouping: false,
});

/**
 * A money figure for humans: "12.00 AED".
 *
 * The `en` branch keeps the exact hand-written shape this replaced — two
 * decimals, no thousands grouping, code after the amount — so adopting it
 * across the storefront changed nothing a customer sees in English. Any
 * locale that is not `ar` gets the English shape; better a readable price in
 * the wrong convention than a formatter that throws on a locale it has never
 * heard of.
 */
export function formatPrice(amount: number | string, locale: string = 'en'): string {
  const n = Number(amount);
  if (locale === 'ar') return AR_PRICE.format(n);
  return `${n.toFixed(2)} AED`;
}
