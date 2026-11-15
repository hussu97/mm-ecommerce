'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { legalEntitiesApi } from '@/lib/pos-api';
import type { EntityDocumentTypeSpec, EntityPaperworkOverview, LegalEntity } from '@/lib/pos-types';
import { summariseEntity } from '@/components/legal-entities/summary';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Badge, Button, Select } from '@/components/ui';
import { ListPage } from '@/components/ui/ListPage';
import { FilterChip } from '@/components/ui/FilterChip';
import type { DataColumn, SortState } from '@/components/ui/DataTable';
import { formatDate } from '@/components/pos/LegalEntityPaperwork';
import {
  initials,
  PaperChip,
  relativeDays,
  type PaperSummary,
  type PaperTone,
} from '@/components/people/EmployeePaperwork';
import { LegalEntityForm } from '@/components/legal-entities/LegalEntityForm';

/**
 * The trade licences orders are booked under, and the state of each one's
 * paperwork — the employees list's layout, for companies.
 *
 * Each row says what most needs doing ("Establishment card expires in 9 days",
 * "Chamber of Commerce membership not on file", "All documents valid") with a
 * labelled chip per company-wide document — trade licence, VAT certificate
 * when registered, establishment card, labour card, chamber, Corporate Tax,
 * MoA — and one for the premises documents on file, coloured by the expiry
 * popup's thresholds. Chips above filter to who needs attention, who is due
 * within 60 days, and who is missing a document. A row opens the entity's page.
 */

type StatusFilter = 'active' | 'inactive' | 'all';
type PaperFilter = 'all' | 'attention' | 'soon' | 'missing';
type Row = LegalEntity & { paper: PaperSummary };

const HEADLINE_TONE: Record<PaperTone, string> = {
  expired: 'text-red-700',
  due: 'text-red-700',
  soon: 'text-amber-800',
  missing: 'text-gray-500',
  ok: 'text-green-700',
};

const ATTENTION_SORT: SortState = { key: 'paperwork', direction: 'asc' };

export default function LegalEntitiesPage() {
  const router = useRouter();
  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [overview, setOverview] = useState<EntityPaperworkOverview[]>([]);
  const [specs, setSpecs] = useState<EntityDocumentTypeSpec[]>([]);
  const [status, setStatus] = useState<StatusFilter>('active');
  const [paperFilter, setPaperFilter] = useState<PaperFilter>('all');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const [list, docs, types] = await Promise.all([
        legalEntitiesApi.list(),
        legalEntitiesApi.documentOverview(),
        legalEntitiesApi.documentTypes(),
      ]);
      setEntities(list);
      setOverview(docs);
      setSpecs(types);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load the legal entities.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rows: Row[] = useMemo(() => {
    const q = search.trim().toLowerCase();
    return entities
      .filter((e) => (status === 'all' ? true : status === 'active' ? e.is_active : !e.is_active))
      .filter(
        (e) =>
          !q ||
          [e.legal_name, e.brand_name, e.reference, e.tax_number, e.trade_license_number].some((v) =>
            v?.toLowerCase().includes(q),
          ),
      )
      .map((e) => ({
        ...e,
        paper: summariseEntity(
          e,
          overview.find((o) => o.legal_entity_id === e.id),
          specs,
        ),
      }));
  }, [entities, overview, specs, status, search]);

  const counts = useMemo(
    () => ({
      all: rows.length,
      attention: rows.filter((r) => r.paper.urgent > 0).length,
      soon: rows.filter((r) => r.paper.soon > 0).length,
      missing: rows.filter((r) => r.paper.missing > 0).length,
    }),
    [rows],
  );
  const shown = rows.filter((r) =>
    paperFilter === 'attention'
      ? r.paper.urgent > 0
      : paperFilter === 'soon'
        ? r.paper.soon > 0
        : paperFilter === 'missing'
          ? r.paper.missing > 0
          : true,
  );

  const columns: DataColumn<Row>[] = [
    {
      header: 'Legal entity',
      priority: 'primary',
      sortable: true,
      sortKey: 'name',
      sortAccessor: (row) => row.brand_name.toLowerCase(),
      render: (row) => (
        <span className="flex min-w-0 items-center gap-3">
          <span
            className={cn(
              'flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full border border-gray-100 font-display text-sm',
              row.is_active ? 'bg-primary/10 text-primary' : 'bg-gray-100 text-gray-400',
            )}
            aria-hidden
          >
            {row.logo_url ? (
              // A public product-images URL.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={row.logo_url} alt="" className="h-full w-full bg-white object-contain p-1" />
            ) : (
              initials(row.brand_name)
            )}
          </span>
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-gray-800">{row.brand_name}</span>
              {!row.is_active && <Badge>Inactive</Badge>}
            </span>
            <span className="block truncate text-xs font-normal text-gray-500">
              {[row.legal_name !== row.brand_name ? row.legal_name : null, row.vat_registered ? `TRN ${row.tax_number ?? '—'}` : 'Not VAT-registered']
                .filter(Boolean)
                .join(' · ')}
            </span>
          </span>
        </span>
      ),
    },
    {
      header: 'Paperwork',
      priority: 'secondary',
      sortable: true,
      sortKey: 'paperwork',
      sortAccessor: (row) => row.paper.rank,
      render: (row) =>
        specs.length === 0 ? null : (
          <span className="block min-w-0 space-y-1.5">
            <span className={cn('block text-sm', HEADLINE_TONE[row.paper.tone])}>{row.paper.headline}</span>
            <span className="flex flex-wrap gap-1">
              {row.paper.items.map((item) => (
                <PaperChip key={item.spec.doc_type} item={item} />
              ))}
            </span>
          </span>
        ),
    },
    {
      header: 'Next expiry',
      priority: 'desktop',
      sortable: true,
      sortKey: 'next',
      sortAccessor: (row) => row.paper.next?.expiry ?? '9999-12-31',
      className: 'whitespace-nowrap',
      render: (row) =>
        row.paper.next ? (
          <span className="block text-sm">
            <span className="block text-gray-800">{row.paper.next.spec.label}</span>
            <span className="block text-xs text-gray-500">
              {formatDate(row.paper.next.expiry)} · {relativeDays(row.paper.next.expiry!)}
            </span>
          </span>
        ) : (
          <span className="text-xs text-gray-400">—</span>
        ),
    },
  ];

  return (
    <ListPage
      title="Legal entities"
      description="The trade licences orders are booked under, and every licence, card, certificate and permit each one holds. Assign an entity to a branch channel on the Branches page."
      search={{ value: search, onChange: setSearch, placeholder: 'Search name, TRN, licence…' }}
      toolbar={
        <div className="w-full sm:w-36">
          <Select
            aria-label="Status"
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
            options={[
              { value: 'active', label: 'Active' },
              { value: 'inactive', label: 'Inactive' },
              { value: 'all', label: 'All' },
            ]}
          />
        </div>
      }
      primaryAction={
        <Button onClick={() => setCreating(true)}>
          <span className="material-icons text-[16px]" aria-hidden>
            add_business
          </span>
          New legal entity
        </Button>
      }
      filterBar={
        !loading && rows.length > 0 && specs.length > 0 ? (
          <div className="-mx-1 mb-4 flex gap-2 overflow-x-auto px-1 pb-1" role="group" aria-label="Filter by paperwork">
            <FilterChip active={paperFilter === 'all'} count={counts.all} onClick={() => setPaperFilter('all')}>
              All
            </FilterChip>
            <FilterChip
              active={paperFilter === 'attention'}
              count={counts.attention}
              tone="red"
              onClick={() => setPaperFilter('attention')}
            >
              Needs attention
            </FilterChip>
            <FilterChip
              active={paperFilter === 'soon'}
              count={counts.soon}
              tone="amber"
              onClick={() => setPaperFilter('soon')}
            >
              Due within 60 days
            </FilterChip>
            <FilterChip
              active={paperFilter === 'missing'}
              count={counts.missing}
              tone="gray"
              onClick={() => setPaperFilter('missing')}
            >
              Missing documents
            </FilterChip>
          </div>
        ) : undefined
      }
      loading={loading}
      loadError={loadError}
      onRetry={() => void load()}
      columns={columns}
      rows={shown}
      rowKey={(row) => row.id}
      onRowClick={(row) => router.push(`/legal-entities/${row.id}`)}
      defaultSort={ATTENTION_SORT}
      emptyMessage={paperFilter !== 'all' ? 'Nothing matches this filter.' : 'No legal entities here.'}
    >
      {creating && (
        <LegalEntityForm
          onClose={() => setCreating(false)}
          onSaved={(saved) => router.push(`/legal-entities/${saved.id}`)}
        />
      )}
    </ListPage>
  );
}
