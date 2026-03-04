import type { Metadata } from 'next';

export const metadata: Metadata = { title: 'Orders' };

export default function OrdersPage() {
  return (
    <div>
      <h1 className="font-display text-2xl text-gray-800 mb-2">Orders</h1>
      <p className="text-sm text-gray-400 font-body">Full order management coming in Prompt 16.</p>
    </div>
  );
}
