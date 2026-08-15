/**
 * The checkout's section chrome. Two presentational pieces, no state.
 *
 * Sections are set off by a hairline and a quiet caption rather than a display
 * heading and a rule each. Six full headings turned a form of about a dozen
 * fields into a page with no visible end.
 */

import { Icon } from '@/components/ui/Icon';

export function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="py-5 border-t border-gray-100 first:border-t-0 first:pt-0">
      <h2 className="font-body text-[11px] uppercase tracking-[0.2em] text-gray-400 mb-3">
        {label}
      </h2>
      {children}
    </section>
  );
}

/** One tappable choice: icon, label, and what it costs. */
export function ChoiceRow({
  selected, onSelect, icon, title, subtitle, trailing,
}: {
  selected: boolean;
  onSelect: () => void;
  icon: string;
  title: string;
  subtitle?: string;
  trailing: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`w-full flex items-center gap-3 px-3.5 py-3 border rounded-sm text-start transition-colors ${
        selected ? 'border-primary bg-primary/5' : 'border-gray-200 hover:border-primary/40'
      }`}
    >
      <Icon name={icon} className={`text-xl ${selected ? 'text-primary' : 'text-gray-400'}`} />
      <span className="flex-1 min-w-0">
        <span className="block font-body text-sm text-gray-800">{title}</span>
        {subtitle && <span className="block font-body text-xs text-gray-400 mt-0.5">{subtitle}</span>}
      </span>
      <span className="font-body text-sm shrink-0">{trailing}</span>
    </button>
  );
}
