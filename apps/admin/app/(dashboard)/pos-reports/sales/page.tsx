'use client';

import { useSearchParams } from 'next/navigation';

import { windowFromParams } from '../report-window';
import { SalesTab } from '../tabs/SalesTab';

export default function SalesReportPage() {
  const { window } = windowFromParams(useSearchParams());
  return <SalesTab window={window} />;
}
