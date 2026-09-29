import { redirect } from 'next/navigation';

// The VAT report moved to Profit & Loss (its VAT tab), where it shares that
// page's date and entity filters. Kept as a redirect so bookmarks still land,
// carrying the reporting window over.
export default async function VatReportRedirect({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams({ tab: 'vat' });
  for (const key of ['from', 'to'] as const) {
    const value = params[key];
    if (typeof value === 'string' && value) query.set(key, value);
  }
  redirect(`/profit-loss?${query.toString()}`);
}
