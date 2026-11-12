'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { complianceApi, type ExpiringDocument } from '@/lib/people-api';
import { dismissExpiryReminder, isExpiryReminderDismissed } from '@/lib/expiry-reminder';
import { Badge, Button } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';
import { formatDate } from '@/components/pos/LegalEntityPaperwork';

/**
 * The super-admin's warning about paperwork that lapses within 15 days: each
 * active employee's current visa, Emirates ID, labour card and Occupational
 * Health Card, and
 * each legal entity's current trade licence. Already-expired documents stay on
 * it until a renewal is entered.
 *
 * Loaded once per console load. The popup shows on the first load of a browser
 * session and "Remind me later" hides it until the session ends; the sidebar
 * badge keeps the count visible meanwhile. Nothing is fetched or shown for
 * anyone who is not a super-admin — the API would refuse them anyway.
 */

/** The owner's lead time, in days. */
export const EXPIRY_WARNING_DAYS = 15;
type ExpiringState = {
  items: ExpiringDocument[];
  refresh: () => void;
};

const ExpiringContext = createContext<ExpiringState>({ items: [], refresh: () => {} });

export function useExpiringDocuments(): ExpiringState {
  return useContext(ExpiringContext);
}

export function ExpiringDocumentsProvider({
  enabled,
  children,
}: {
  enabled: boolean;
  children: React.ReactNode;
}) {
  const [items, setItems] = useState<ExpiringDocument[]>([]);
  const [open, setOpen] = useState(false);

  const load = useCallback(
    async (initial: boolean) => {
      if (!enabled) return;
      try {
        const res = await complianceApi.expiringDocuments(EXPIRY_WARNING_DAYS);
        setItems(res.items);
        if (initial && res.items.length > 0 && !isExpiryReminderDismissed()) setOpen(true);
      } catch {
        // A warning that fails to load must not break the console.
      }
    },
    [enabled],
  );

  useEffect(() => {
    void load(true);
  }, [load]);

  const value = useMemo(() => ({ items, refresh: () => void load(false) }), [items, load]);

  function dismiss() {
    dismissExpiryReminder();
    setOpen(false);
  }

  return (
    <ExpiringContext.Provider value={value}>
      {children}
      {open && items.length > 0 && <ExpiringDocumentsModal items={items} onClose={dismiss} />}
    </ExpiringContext.Provider>
  );
}

export function DaysLeftBadge({ days }: { days: number }) {
  if (days < 0) return <Badge variant="danger">Expired {-days}d ago</Badge>;
  if (days === 0) return <Badge variant="danger">Expires today</Badge>;
  return <Badge variant={days <= 7 ? 'danger' : 'warning'}>{days}d left</Badge>;
}

/** Where to go to upload the renewal. */
export function renewalHref(item: ExpiringDocument): string {
  return item.owner_kind === 'employee'
    ? `/employees/${item.owner_id}?add=${item.doc_type}`
    : '/pos-config/legal-entities';
}

function ExpiringDocumentsModal({
  items,
  onClose,
}: {
  items: ExpiringDocument[];
  onClose: () => void;
}) {
  return (
    <Modal title="Documents expiring soon" onClose={onClose} wide>
      <p className="mb-4 text-xs font-body text-gray-500">
        These lapse within {EXPIRY_WARNING_DAYS} days (or already have). Upload the renewed
        document so the record stays current.
      </p>
      <ul className="divide-y divide-gray-100 border border-gray-100" data-testid="expiring-documents">
        {items.map((item) => (
          <li
            key={`${item.owner_kind}-${item.owner_id}-${item.doc_type}`}
            className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-body text-gray-800">{item.owner_name}</p>
              <p className="text-xs font-body text-gray-500">
                {item.doc_label}
                {item.document_number ? ` · ${item.document_number}` : ''} · expires{' '}
                {formatDate(item.expiry_date)}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <DaysLeftBadge days={item.days_left} />
              <Link
                href={renewalHref(item)}
                onClick={onClose}
                className="text-xs font-body uppercase tracking-widest text-primary hover:underline"
              >
                Upload new
              </Link>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex justify-end">
        <Button variant="secondary" onClick={onClose}>
          Remind me later
        </Button>
      </div>
    </Modal>
  );
}
