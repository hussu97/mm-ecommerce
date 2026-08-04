'use client';

/**
 * The counter's orders now live on the one orders screen.
 *
 * Kept as a redirect rather than deleted: this path is in people's bookmarks
 * and in the sidebar of anyone with a stale tab open, and a 404 is a worse
 * answer than the screen they wanted.
 */

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function PosOrdersPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace('/orders?channel=counter');
  }, [router]);
  return null;
}
