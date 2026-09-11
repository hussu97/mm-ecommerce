import { Suspense } from 'react';

import { PosReportsTabs } from './PosReportsTabs';
import { ReportWindow } from './_window';

/**
 * One frame for the six POS report screens. The heading, the shared branch +
 * date-range window and the tab bar are rendered here, once, above whichever
 * report is showing — the window sits in the URL, so every tab reads the same
 * one. `useSearchParams` needs a Suspense boundary, so the interactive region
 * (window, tabs and the tab body) lives inside one.
 */
export default function PosReportsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <h1 className="font-display text-xl text-primary tracking-wide mb-1">POS Reports</h1>
      <p className="mb-3 text-xs text-gray-500 font-body">
        Scoped by trading day, so sales after midnight report against the day they belong to.
      </p>
      <Suspense>
        <ReportWindow />
        <PosReportsTabs />
        <div className="max-w-[1400px]">{children}</div>
      </Suspense>
    </div>
  );
}
