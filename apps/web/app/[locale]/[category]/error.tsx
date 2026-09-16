'use client';

import { SegmentError } from '@/components/ui/SegmentError';

export default function CategoryError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <SegmentError
      {...props}
      title="This Page Couldn't Load"
      message="We could not load these products just now. Please try again."
      backHref="/all-products"
      backLabel="Browse All Products"
    />
  );
}
