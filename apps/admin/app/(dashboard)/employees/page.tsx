'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { employeesApi, type EmployeeListItem } from '@/lib/people-api';
import { branchesApi, legalEntitiesApi, staffApi } from '@/lib/pos-api';
import type { Branch, LegalEntity, Staff } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Select } from '@/components/ui';
import { ListPage } from '@/components/ui/ListPage';
import type { DataColumn } from '@/components/ui/DataTable';
import { EmployeeForm } from '@/components/people/EmployeeForm';
import { DocumentExpiry } from '@/components/people/DocumentExpiry';

/**
 * Employees and the state of their paperwork — super-admin only. One row per
 * person with each document's current expiry, so a lapse is visible at a
 * glance. One shop's staff is a few dozen rows, so the list is not paginated.
 */

type StatusFilter = 'active' | 'left' | 'all';

export default function EmployeesPage() {
  const router = useRouter();
  const [rows, setRows] = useState<EmployeeListItem[]>([]);
  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [accounts, setAccounts] = useState<Staff[]>([]);
  const [status, setStatus] = useState<StatusFilter>('active');
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
    legalEntitiesApi.list().then(setEntities).catch(() => {});
    branchesApi.list().then(setBranches).catch(() => {});
    staffApi.list().then(setAccounts).catch(() => {});
  }, []);

  const entityName = (id: string | null | undefined) =>
    entities.find((e) => e.id === id)?.legal_name ?? '—';

  const docColumn = (type: string, header: string): DataColumn<EmployeeListItem> => ({
    header,
    priority: 'secondary',
    render: (row) => <DocumentExpiry expiry={row.current_documents?.[type]?.expiry_date} />,
  });

  const columns: DataColumn<EmployeeListItem>[] = [
    {
      header: 'Name',
      priority: 'primary',
      render: (row) => (
        <span className="inline-flex flex-wrap items-center gap-2">
          <span className="font-medium text-gray-800">{row.full_name}</span>
          {row.status === 'left' && <Badge>Left</Badge>}
        </span>
      ),
    },
    { header: 'Job title', priority: 'meta', render: (row) => row.job_title ?? '—' },
    { header: 'Sponsor', priority: 'desktop', render: (row) => entityName(row.legal_entity_id) },
    docColumn('visa', 'Visa'),
    docColumn('emirates_id', 'Emirates ID'),
    docColumn('labour_card', 'Labour card'),
    docColumn('ohc', 'Health card'),
    docColumn('medical_insurance', 'Insurance'),
    docColumn('wps_card', 'WPS card'),
    docColumn('passport', 'Passport'),
  ];

  return (
    <ListPage
      title="Employees"
      description="Passports, visas, Emirates IDs, labour cards, health cards, medical insurance and WPS salary cards — every renewal kept on file. Visible to super-admins only."
      search={{ value: search, onChange: setSearch, placeholder: 'Search name, number…' }}
      toolbar={
        <div className="w-full sm:w-36">
          <Select
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
      primaryAction={<Button onClick={() => setCreating(true)}>New employee</Button>}
      loading={loading}
      loadError={loadError}
      onRetry={() => void load()}
      columns={columns}
      rows={rows}
      rowKey={(row) => row.id}
      onRowClick={(row) => router.push(`/employees/${row.id}`)}
      emptyMessage={status === 'active' ? 'No employees yet — add the first one.' : 'Nobody here.'}
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
