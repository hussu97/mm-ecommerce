export interface Occasion {
  icon: string;
  label: string;
}

export interface CaterContent {
  title?: string;
  occasions?: Occasion[];
}

export function CaterSection({ c }: { c: CaterContent }) {
  const occasions = c.occasions ?? [];

  return (
    <section aria-label="Occasions we cater to" className="py-16 bg-[#f9f5f0]">
      <div className="max-w-7xl mx-auto px-4">

        <h2 className="font-display text-2xl sm:text-3xl text-center text-gray-800 uppercase tracking-widest mb-10">
          {c.title}
        </h2>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          {occasions.map(({ icon, label }) => (
            <div
              key={label}
              className="flex flex-col items-center gap-3 p-6 bg-white border border-secondary/40 hover:border-primary hover:shadow-sm transition-all duration-200"
            >
              <span className="text-3xl" role="img" aria-label={label}>
                {icon}
              </span>
              <span className="font-body text-xs uppercase tracking-widest text-gray-600">
                {label}
              </span>
            </div>
          ))}
        </div>

      </div>
    </section>
  );
}
