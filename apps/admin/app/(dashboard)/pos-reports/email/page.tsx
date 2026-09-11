'use client';

import { useSearchParams } from 'next/navigation';

import { windowFromParams } from '../report-window';
import { EmailTab } from '../tabs/EmailTab';

export default function EmailReportPage() {
  const { window } = windowFromParams(useSearchParams());
  return <EmailTab window={window} />;
}
