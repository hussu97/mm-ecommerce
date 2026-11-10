import { Badge } from '@/components/ui';
import { daysUntil, formatDate } from '@/components/pos/LegalEntityPaperwork';
import { EXPIRY_WARNING_DAYS } from './ExpiringDocuments';

/** A document's expiry with how soon it lapses: red once inside the 15-day
 *  warning window (or expired), amber within 60 days, plain otherwise. */
export function DocumentExpiry({ expiry }: { expiry: string | null | undefined }) {
  if (!expiry) return <span className="text-gray-400">—</span>;
  const days = daysUntil(expiry);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span>{formatDate(expiry)}</span>
      {days < 0 ? (
        <Badge variant="danger">Expired</Badge>
      ) : days <= EXPIRY_WARNING_DAYS ? (
        <Badge variant="danger">{days === 0 ? 'Today' : `${days}d left`}</Badge>
      ) : days <= 60 ? (
        <Badge variant="warning">{days}d left</Badge>
      ) : null}
    </span>
  );
}
