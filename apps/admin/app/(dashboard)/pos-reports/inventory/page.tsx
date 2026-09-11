'use client';

import { useSearchParams } from 'next/navigation';

import { windowFromParams } from '../report-window';
import { InventoryTab } from '../tabs/InventoryTab';

export default function InventoryReportPage() {
  const { window, branch } = windowFromParams(useSearchParams());
  return <InventoryTab window={window} branchId={branch} />;
}
