'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { complianceApi, type ExpiringDocument } from '@/lib/people-api';
import { dismissExpiryReminder, isExpiryReminderDismissed } from '@/lib/expiry-reminder';
import { Badge, Button } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';
import { formatDate } from '@/components/pos/LegalEntityPaperwork';

/**
 * The super-admin's one warning about paperwork that lapses within 15 days:
 * each active employee's current visa, Emirates ID, labour card, Occupational
 * Health Card, medical insurance and WPS salary card, and each active legal
 * entity's current trade licence, establishment card, labour company card,
 * chamber membership and its shop's tenancy, food permit, Civil Defence
 * certificate, signage permit and pest-control contract. Already-expired
 * documents stay on it until a renewal is entered.
 *
 * Grouped by whose paper it is — companies first, then people — each with a
 * link to their page and a Renew link per document that opens its add form.
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

const ExpiringContext = createContext<ExpiringState>({
  items: [],
  refresh: () => {},
});

export function useExpiringDocuments(): ExpiringState {
  return useContext(ExpiringContext);
}

export function ExpiringDocumentsProvider({ enabled, children }: { enabled: boolean; children: React.ReactNode }) {
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

/** The owner's page. */
export function ownerHref(item: Pick<ExpiringDocument, 'owner_kind' | 'owner_id'>): string {
  return item.owner_kind === 'employee' ? `/employees/${item.owner_id}` : `/legal-entities/${item.owner_id}`;
}

/** Where to go to upload the renewal: the owner's page with the add form open. */
export function renewalHref(item: ExpiringDocument): string {
  return `${ownerHref(item)}?add=${item.doc_type}`;
}

type OwnerGroup = {
  key: string;
  kind: ExpiringDocument['owner_kind'];
  id: string;
  name: string;
  items: ExpiringDocument[];
};

/** Companies first, then people; each owner's papers soonest first, owners by
 *  their most urgent paper. */
export function groupByOwner(items: ExpiringDocument[]): OwnerGroup[] {
  const groups = new Map<string, OwnerGroup>();
  for (const item of items) {
    const key = `${item.owner_kind}-${item.owner_id}`;
    const group = groups.get(key) ?? {
      key,
      kind: item.owner_kind,
      id: item.owner_id,
      name: item.owner_name,
      items: [],
    };
    group.items.push(item);
    groups.set(key, group);
  }
  const soonest = (g: OwnerGroup) => Math.min(...g.items.map((i) => i.days_left));
  return [...groups.values()]
    .map((g) => ({
      ...g,
      items: [...g.items].sort((a, b) => a.days_left - b.days_left),
    }))
    .sort(
      (a, b) =>
        Number(a.kind === 'employee') - Number(b.kind === 'employee') ||
        soonest(a) - soonest(b) ||
        a.name.localeCompare(b.name),
    );
}

function OwnerSection({
  title,
  icon,
  list,
  onClose,
}: {
  title: string;
  icon: string;
  list: OwnerGroup[];
  onClose: () => void;
}) {
  if (list.length === 0) return null;
  return (
    <section>
      <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-widest text-gray-500">
        <span className="material-icons text-[16px]" aria-hidden>
          {icon}
        </span>
        {title}
      </h3>
      <ul className="space-y-2">
        {list.map((group) => (
          <li key={group.key} className="border border-gray-200" data-testid={`expiring-${group.key}`}>
            <div className="flex items-center justify-between gap-2 border-b border-gray-100 bg-gray-50 px-3 py-2">
              <p className="min-w-0 truncate text-sm font-medium text-gray-800">{group.name}</p>
              <Link
                href={ownerHref({ owner_kind: group.kind, owner_id: group.id })}
                onClick={onClose}
                className="inline-flex shrink-0 items-center gap-0.5 text-xs uppercase tracking-widest text-gray-500 hover:text-primary"
              >
                Open
                <span className="material-icons text-[14px]" aria-hidden>
                  chevron_right
                </span>
              </Link>
            </div>
            <ul className="divide-y divide-gray-100">
              {group.items.map((item) => (
                <li
                  key={item.doc_type}
                  className="flex flex-col gap-2 px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="text-sm text-gray-800">
                      {item.doc_label}
                    </p>
                    <p className="text-xs text-gray-500">
                      {item.document_number ? `${item.document_number} · ` : ''}
                      expires {formatDate(item.expiry_date)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <DaysLeftBadge days={item.days_left} />
                    <Link
                      href={renewalHref(item)}
                      onClick={onClose}
                      className="text-xs uppercase tracking-widest text-primary hover:underline"
                    >
                      Renew
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ExpiringDocumentsModal({ items, onClose }: { items: ExpiringDocument[]; onClose: () => void }) {
  const groups = groupByOwner(items);
  const expired = items.filter((i) => i.days_left < 0).length;
  const companies = groups.filter((g) => g.kind === 'legal_entity');
  const people = groups.filter((g) => g.kind === 'employee');
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

  return (
    <Modal title="Upcoming renewals" onClose={onClose} wide>
      <p className="mb-4 text-xs font-body text-gray-500">
        {plural(items.length, 'document lapses', 'documents lapse')} within {EXPIRY_WARNING_DAYS} days
        {expired > 0 ? ` — ${expired} already expired` : ''}, across{' '}
        {[
          companies.length ? plural(companies.length, 'company', 'companies') : null,
          people.length ? plural(people.length, 'employee', 'employees') : null,
        ]
          .filter(Boolean)
          .join(' and ')}
        . Upload each renewed document so the record stays current.
      </p>
      <div className="space-y-5 font-body" data-testid="expiring-documents">
        <OwnerSection title="Companies" icon="domain" list={companies} onClose={onClose} />
        <OwnerSection title="Employees" icon="assignment_ind" list={people} onClose={onClose} />
      </div>
      <div className="mt-4 flex justify-end">
        <Button variant="secondary" onClick={onClose}>
          Remind me later
        </Button>
      </div>
    </Modal>
  );
}
