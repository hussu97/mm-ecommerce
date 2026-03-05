import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Terms & Conditions',
  description: 'Terms and conditions for Melting Moments Cakes.',
};

export default function TermsPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      <h1 className="font-display text-3xl text-primary uppercase tracking-widest mb-6">
        Terms &amp; Conditions
      </h1>
      <div className="h-px bg-secondary/40 mb-8" />
      <p className="font-body text-gray-500 text-sm leading-relaxed">
        This page is coming soon. Please check back shortly.
      </p>
    </div>
  );
}
