'use client';

import { SegmentError } from '@/components/ui/SegmentError';

export default function CheckoutError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <SegmentError
      {...props}
      title="Checkout Hit a Snag"
      message="Your order was not placed and you have not been charged. Try again — your basket is still saved."
      backHref="/cart"
      backLabel="Back to Cart"
    />
  );
}
