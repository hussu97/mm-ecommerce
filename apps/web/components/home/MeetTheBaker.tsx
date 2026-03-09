import Image from 'next/image';
import Link from 'next/link';

export interface BakerContent {
  label?: string;
  quote?: string;
  body?: string;
  button_text?: string;
  button_href?: string;
}

export function MeetTheBaker({ c, locale }: { c: BakerContent; locale: string }) {
  return (
    <section aria-label="Meet the baker" className="relative overflow-hidden">
      <div className="relative h-[480px] sm:h-[560px]">

        {/* Background photo */}
        <Image
          src="/images/photos/person_shot_4.jpg"
          alt="Fatema Abbasi baking in her kitchen"
          fill
          sizes="100vw"
          className="object-cover object-center"
        />

        {/* Primary overlay */}
        <div className="absolute inset-0 bg-primary/75" />

        {/* Content */}
        <div className="relative z-10 h-full flex items-center justify-center px-4">
          <div className="text-center text-white max-w-xl">

            {/* Bordered label */}
            <span className="inline-block border border-white/60 text-xs font-body uppercase tracking-[0.3em] px-4 py-1.5 mb-6">
              {c.label}
            </span>

            <h2 className="font-display text-3xl sm:text-4xl italic mb-5 leading-snug">
              &ldquo;{c.quote}&rdquo;
            </h2>

            <p className="font-body text-white/80 text-sm leading-relaxed mb-8 max-w-md mx-auto">
              {c.body}
            </p>

            <Link
              href={`/${locale}${c.button_href ?? '/about'}`}
              className="inline-flex items-center gap-2 border border-white text-white text-xs font-body uppercase tracking-widest px-6 py-3 hover:bg-white hover:text-primary transition-all duration-300"
            >
              {c.button_text}
            </Link>

          </div>
        </div>

      </div>
    </section>
  );
}
