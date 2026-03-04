import type { Metadata } from 'next';

export const metadata: Metadata = { title: 'Promo Codes' };

export default function PromoCodesPage() {
  return (
    <div>
      <h1 className="font-display text-2xl text-gray-800 mb-2">Promo Codes</h1>
      <p className="text-sm text-gray-400 font-body">Promo code management coming in Prompt 16.</p>
    </div>
  );
}
