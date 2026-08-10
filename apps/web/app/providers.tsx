'use client';

import { AuthProvider } from '@/lib/auth-context';
import { CartProvider } from '@/lib/cart-context';
import { LocationProvider } from '@/lib/location/LocationProvider';
import { ToastProvider } from '@/components/ui';
import { ClarityIdentity } from '@/components/analytics/ClarityIdentity';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      {/* Renders nothing; it needs to be inside the provider whose user it
          reports. Every page has it, which is the point — a customer signs in
          from anywhere and their session should be attributable from there on. */}
      <ClarityIdentity />
      {/* Inside auth, because a signed-in customer's default address is the
          strongest thing we know about where they are. */}
      <LocationProvider>
        <CartProvider>
          <ToastProvider>
            {children}
          </ToastProvider>
        </CartProvider>
      </LocationProvider>
    </AuthProvider>
  );
}
