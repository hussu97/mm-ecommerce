'use client';

import { useSearchParams } from 'next/navigation';

import { windowFromParams } from '../report-window';
import { TaxesTab } from '../tabs/TaxesTab';

export default function TaxesReportPage() {
  const { window } = windowFromParams(useSearchParams());
  return <TaxesTab window={window} />;
}
