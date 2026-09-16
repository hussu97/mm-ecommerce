'use client';

import { SegmentError } from '@/components/ui/SegmentError';

export default function AccountError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <SegmentError
      {...props}
      title="Something Went Wrong"
      message="We could not load your account just now. Please try again."
      backHref="/"
      backLabel="Back to Home"
    />
  );
}
