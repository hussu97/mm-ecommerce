import { LogsTabs } from './LogsTabs';

/**
 * One frame for the three log screens. The tab bar is rendered here, once,
 * rather than repeated at the top of each page — the same tabs sit above
 * whichever log is showing.
 */
export default function LogsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <LogsTabs />
      {children}
    </div>
  );
}
