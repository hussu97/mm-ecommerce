const OCCASIONS = [
  { icon: '🎂', label: 'Birthdays' },
  { icon: '💍', label: 'Weddings' },
  { icon: '💼', label: 'Corporate' },
  { icon: '🌙', label: 'Eid' },
  { icon: '✨', label: 'Ramadan' },
  { icon: '🎉', label: 'Celebrations' },
];

export function CaterSection() {
  return (
    <section aria-label="Occasions we cater to" className="py-16 bg-[#f9f5f0]">
      <div className="max-w-7xl mx-auto px-4">

        <h2 className="font-display text-2xl sm:text-3xl text-center text-gray-800 uppercase tracking-widest mb-10">
          We Cater To
        </h2>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          {OCCASIONS.map(({ icon, label }) => (
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
