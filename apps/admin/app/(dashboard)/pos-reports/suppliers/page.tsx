'use client';

import { useSearchParams } from 'next/navigation';

import { windowFromParams } from '../report-window';
import { SuppliersTab } from '../tabs/SuppliersTab';

export default function SuppliersReportPage() {
  const { window } = windowFromParams(useSearchParams());
  return <SuppliersTab window={window} />;
}
