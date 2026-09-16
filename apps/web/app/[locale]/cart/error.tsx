'use client';

import { SegmentError } from '@/components/ui/SegmentError';

export default function CartError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <SegmentError
      {...props}
      title="Your Cart Couldn't Load"
      message="We could not load your basket just now. Please try again."
      backHref="/all-products"
      backLabel="Continue Shopping"
    />
  );
}
