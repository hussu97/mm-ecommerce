import Link from 'next/link';
import type { Metadata } from 'next';

export const metadata: Metadata = { title: '404 — Page Not Found' };

export default function NotFound() {
  return (
    <div className="min-h-[70vh] flex flex-col items-center justify-center px-4 text-center">
      <p className="font-display text-8xl sm:text-[10rem] text-secondary/60 leading-none select-none">
        404
      </p>
      <h1 className="font-display text-2xl sm:text-3xl text-primary uppercase tracking-widest mt-2 mb-4">
        Page Not Found
      </h1>
      <p className="font-body text-sm text-gray-500 max-w-sm mb-8">
        The page you&apos;re looking for doesn&apos;t exist. It may have been moved, or the link is incorrect.
      </p>
      <div className="flex flex-col sm:flex-row gap-3">
        <Link
          href="/"
          className="px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
        >
          Back to Home
        </Link>
        <Link
          href="/contact"
          className="px-6 py-3 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors"
        >
          Contact Us
        </Link>
      </div>
      {/* Decorative line */}
      <div className="mt-12 w-16 h-0.5 bg-secondary/40" />
      <p className="font-body text-xs text-gray-400 italic mt-3">Made with 100% Love</p>
    </div>
  );
}
