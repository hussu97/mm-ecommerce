'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { employeesApi, type EmployeeDocumentTypeSpec, type EmployeeListItem } from '@/lib/people-api';
import { branchesApi, legalEntitiesApi, staffApi } from '@/lib/pos-api';
import type { Branch, LegalEntity, Staff } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Badge, Button, Select } from '@/components/ui';
import { ListPage } from '@/components/ui/ListPage';
import type { DataColumn, SortState } from '@/components/ui/DataTable';
import { EmployeeForm } from '@/components/people/EmployeeForm';
import { formatDate } from '@/components/pos/LegalEntityPaperwork';
import {
  initials,
  PaperChip,
  relativeDays,
  summarisePaperwork,
  type PaperSummary,
  type PaperTone,
} from '@/components/people/EmployeePaperwork';

/**
 * Employees and the state of their paperwork — super-admin only.
 *
 * Each row says, in words, what most needs doing for that person ("Health
 * card expired 12 days ago", "Insurance not on file", "All documents valid")
 * with a labelled chip per document type coloured by the expiry popup's
 * thresholds — never a column of unlabelled dates. The list opens most urgent
 * first; chips above it filter to who needs attention, who is due within 60
 * days, and who is missing a document. One shop's staff is a few dozen rows,
 * so it is not paginated.
 */

type StatusFilter = 'active' | 'left' | 'all';
type PaperFilter = 'all' | 'attention' | 'soon' | 'missing';
type Row = EmployeeListItem & { paper: PaperSummary };

const HEADLINE_TONE: Record<PaperTone, string> = {
  expired: 'text-red-700',
  due: 'text-red-700',
  soon: 'text-amber-800',
  missing: 'text-gray-500',
  ok: 'text-green-700',
};

const ATTENTION_SORT: SortState = { key: 'paperwork', direction: 'asc' };

function FilterChip({
  active,
  count,
  tone,
  onClick,
  children,
}: {
  active: boolean;
  count: number;
  tone?: 'red' | 'amber' | 'gray';
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'inline-flex shrink-0 items-center gap-2 whitespace-nowrap border px-3 py-1.5 text-xs font-body transition-colors',
        active
          ? 'border-primary bg-primary text-white'
          : 'border-gray-200 bg-white text-gray-700 hover:border-primary/50',
      )}
    >
      {children}
      <span
        className={cn(
          'min-w-[1.25rem] rounded-full px-1.5 text-center text-[11px]',
          active
            ? 'bg-white/20 text-white'
            : count > 0 && tone === 'red'
              ? 'bg-red-100 text-red-700'
              : count > 0 && tone === 'amber'
                ? 'bg-amber-100 text-amber-800'
                : 'bg-gray-100 text-gray-500',
        )}
      >
        {count}
      </span>
    </button>
  );
}

export default function EmployeesPage() {
  const router = useRouter();
  const [rows, setRows] = useState<EmployeeListItem[]>([]);
  const [specs, setSpecs] = useState<EmployeeDocumentTypeSpec[]>([]);
  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [accounts, setAccounts] = useState<Staff[]>([]);
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
      setRows(await employeesApi.list(status, search));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load employees.');
    } finally {
      setLoading(false);
    }
  }, [status, search]);

  useEffect(() => {
    const t = setTimeout(() => void load(), search ? 250 : 0);
    return () => clearTimeout(t);
  }, [load, search]);

  useEffect(() => {
    employeesApi
      .documentTypes()
      .then(setSpecs)
      .catch(() => {});
    legalEntitiesApi
      .list()
      .then(setEntities)
      .catch(() => {});
    branchesApi
      .list()
      .then(setBranches)
      .catch(() => {});
    staffApi
      .list()
      .then(setAccounts)
      .catch(() => {});
  }, []);

  const withPaper: Row[] = useMemo(
    () => rows.map((r) => ({ ...r, paper: summarisePaperwork(r.current_documents ?? {}, specs) })),
    [rows, specs],
  );
  const counts = useMemo(
    () => ({
      all: withPaper.length,
      attention: withPaper.filter((r) => r.paper.urgent > 0).length,
      soon: withPaper.filter((r) => r.paper.soon > 0).length,
      missing: withPaper.filter((r) => r.paper.missing > 0).length,
    }),
    [withPaper],
  );
  const shown = withPaper.filter((r) =>
    paperFilter === 'attention'
      ? r.paper.urgent > 0
      : paperFilter === 'soon'
        ? r.paper.soon > 0
        : paperFilter === 'missing'
          ? r.paper.missing > 0
          : true,
  );

  const entityName = (id: string | null | undefined) => entities.find((e) => e.id === id)?.legal_name;
  const branchName = (id: string | null | undefined) => branches.find((b) => b.id === id)?.name;

  const columns: DataColumn<Row>[] = [
    {
      header: 'Employee',
      priority: 'primary',
      sortable: true,
      sortKey: 'name',
      sortAccessor: (row) => row.full_name.toLowerCase(),
      render: (row) => (
        <span className="flex min-w-0 items-center gap-3">
          <span
            className={cn(
              'flex h-9 w-9 shrink-0 items-center justify-center rounded-full font-display text-sm',
              row.status === 'left' ? 'bg-gray-100 text-gray-400' : 'bg-primary/10 text-primary',
            )}
            aria-hidden
          >
            {initials(row.full_name)}
          </span>
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-gray-800">{row.full_name}</span>
              {row.status === 'left' && <Badge>Left</Badge>}
            </span>
            <span className="block truncate text-xs font-normal text-gray-500">
              {[row.job_title, entityName(row.legal_entity_id), branchName(row.branch_id)]
                .filter(Boolean)
                .join(' · ') || 'No job title yet'}
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
      title="Employees"
      description="Every employee's documents and when they expire. Visible to super-admins only."
      search={{ value: search, onChange: setSearch, placeholder: 'Search name, number…' }}
      toolbar={
        <div className="w-full sm:w-36">
          <Select
            aria-label="Employment status"
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
            options={[
              { value: 'active', label: 'Active' },
              { value: 'left', label: 'Left' },
              { value: 'all', label: 'All' },
            ]}
          />
        </div>
      }
      primaryAction={
        <Button onClick={() => setCreating(true)}>
          <span className="material-icons text-[16px]" aria-hidden>
            person_add
          </span>
          New employee
        </Button>
      }
      filterBar={
        !loading && withPaper.length > 0 && specs.length > 0 ? (
          <div
            className="-mx-1 mb-4 flex gap-2 overflow-x-auto px-1 pb-1"
            role="group"
            aria-label="Filter by paperwork"
          >
            <FilterChip active={paperFilter === 'all'} count={counts.all} onClick={() => setPaperFilter('all')}>
              Everyone
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
      onRowClick={(row) => router.push(`/employees/${row.id}`)}
      defaultSort={ATTENTION_SORT}
      emptyMessage={
        paperFilter !== 'all'
          ? 'Nobody matches this filter.'
          : status === 'active'
            ? 'No employees yet — add the first one.'
            : 'Nobody here.'
      }
    >
      {creating && (
        <EmployeeForm
          entities={entities}
          branches={branches}
          accounts={accounts}
          onClose={() => setCreating(false)}
          onSaved={(saved) => router.push(`/employees/${saved.id}`)}
        />
      )}
    </ListPage>
  );
}
