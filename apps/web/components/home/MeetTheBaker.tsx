import Image from 'next/image';
import Link from 'next/link';

export function MeetTheBaker() {
  return (
    <section aria-label="Meet the baker" className="relative overflow-hidden">
      <div className="relative h-[480px] sm:h-[560px]">

        {/* Background photo */}
        <Image
          src="/images/photos/person_shot_4.png"
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
              Meet the Baker
            </span>

            <h2 className="font-display text-3xl sm:text-4xl italic mb-5 leading-snug">
              &ldquo;Every treat is baked<br />with intention and love.&rdquo;
            </h2>

            <p className="font-body text-white/80 text-sm leading-relaxed mb-8 max-w-md mx-auto">
              Hi, I&apos;m Fatema Abbasi — a self-taught baker based in the UAE.
              Melting Moments started as a passion project to bring comfort through
              food, one handcrafted dessert at a time.
            </p>

            <Link
              href="/about"
              className="inline-flex items-center gap-2 border border-white text-white text-xs font-body uppercase tracking-widest px-6 py-3 hover:bg-white hover:text-primary transition-all duration-300"
            >
              Read More
            </Link>

          </div>
        </div>

      </div>
    </section>
  );
}
