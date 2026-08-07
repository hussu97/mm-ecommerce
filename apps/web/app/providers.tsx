'use client';

import { AuthProvider } from '@/lib/auth-context';
import { CartProvider } from '@/lib/cart-context';
import { LocationProvider } from '@/lib/location/LocationProvider';
import { ToastProvider } from '@/components/ui';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
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
