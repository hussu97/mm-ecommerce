import { PosConfigTabs } from './PosConfigTabs';

/**
 * One frame for the five POS Configuration screens. The section title and tab
 * bar are rendered here, once, above whichever resource is showing — rather
 * than repeated at the top of each page.
 */
export default function PosConfigLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div className="px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-3">POS Configuration</h1>
        <PosConfigTabs />
      </div>
      {children}
    </div>
  );
}
