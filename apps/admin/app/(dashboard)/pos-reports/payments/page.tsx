'use client';

import { useSearchParams } from 'next/navigation';

import { windowFromParams } from '../report-window';
import { PaymentsTab } from '../tabs/PaymentsTab';

export default function PaymentsReportPage() {
  const { window } = windowFromParams(useSearchParams());
  return <PaymentsTab window={window} />;
}
