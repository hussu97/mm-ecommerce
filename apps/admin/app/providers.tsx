'use client';

import { AuthProvider } from '@/lib/auth-context';
import { DensityProvider } from '@/lib/density-context';
import { FeedbackProvider } from '@/components/ui/feedback';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <DensityProvider>
        <FeedbackProvider>{children}</FeedbackProvider>
      </DensityProvider>
    </AuthProvider>
  );
}
