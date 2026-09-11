import { DevicesTabs } from './DevicesTabs';

/**
 * One frame for the two Devices screens. The section title and tab bar are
 * rendered here, once, above whichever screen is showing — rather than repeated
 * at the top of each page.
 */
export default function DevicesLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div className="px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-3">Devices &amp; Printers</h1>
        <DevicesTabs />
      </div>
      {children}
    </div>
  );
}
