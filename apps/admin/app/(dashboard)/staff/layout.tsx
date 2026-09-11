import { StaffTabs } from './StaffTabs';

/**
 * One frame for the two Staff screens. The section title and tab bar are
 * rendered here, once, above whichever screen is showing — rather than repeated
 * at the top of each page.
 */
export default function StaffLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div className="px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-3">Staff &amp; Roles</h1>
        <StaffTabs />
      </div>
      {children}
    </div>
  );
}
