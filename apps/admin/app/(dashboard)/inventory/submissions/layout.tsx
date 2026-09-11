import { SubmissionsTabs } from './SubmissionsTabs';

/**
 * One frame for the three Report-submissions sub-tabs. The section title and
 * the sub-tab bar are rendered here, once, above whichever sub-route is showing
 * — the same job `InventoryLayout`/`LogsLayout` do for their sections. This
 * sits inside the inventory frame, so a second (sub-)tab row appears under the
 * inventory tabs; that nesting is intended.
 */
export default function SubmissionsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <h1 className="font-display text-lg text-primary tracking-wide mb-3">Report submissions</h1>
      <SubmissionsTabs />
      {children}
    </div>
  );
}
