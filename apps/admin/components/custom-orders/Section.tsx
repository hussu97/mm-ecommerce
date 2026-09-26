/**
 * One card of a custom-order screen: a small uppercase title, an optional hint
 * and an optional action on the right — the order detail page's card shape.
 */
export function Section({
  title,
  hint,
  children,
  action,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section className="bg-white border border-gray-200 p-4 mb-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">{title}</p>
          {hint && <p className="mt-0.5 text-xs font-body text-gray-500">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
