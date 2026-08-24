'use client';

import { useEffect, useState } from 'react';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { Input, Select, TabBar } from '@/components/ui';

import { defaultWindow, type TabKey } from './report-window';
import { EmailTab } from './tabs/EmailTab';
import { InventoryTab } from './tabs/InventoryTab';
import { PaymentsTab } from './tabs/PaymentsTab';
import { SalesTab } from './tabs/SalesTab';
import { SuppliersTab } from './tabs/SuppliersTab';
import { TaxesTab } from './tabs/TaxesTab';

export default function PosReportsPage() {
  const [tab, setTab] = useState<TabKey>('sales');
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchId, setBranchId] = useState('');
  const [{ from, to }, setRange] = useState(defaultWindow);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  const window = {
    branch_id: branchId || undefined,
    date_from: from || undefined,
    date_to: to || undefined,
  };

  return (
    <div>
      <div className="border-b border-gray-200 px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-1">POS Reports</h1>
        <p className="mb-3 text-xs text-gray-500 font-body">
          Scoped by trading day, so sales after midnight report against the day they belong to.
        </p>
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <Select
            label="Branch"
            value={branchId}
            onChange={(e) => setBranchId(e.target.value)}
            options={branches.map((b) => ({ value: b.id, label: b.name }))}
            placeholder="All branches"
            className="w-52"
          />
          <Input
            label="From"
            type="date"
            value={from}
            onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
            className="w-40"
          />
          <Input
            label="To"
            type="date"
            value={to}
            onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
            className="w-40"
          />
        </div>
        <TabBar
          tabs={[
            { key: 'sales', label: 'Sales' },
            { key: 'payments', label: 'Payments' },
            { key: 'taxes', label: 'Taxes' },
            { key: 'inventory', label: 'Inventory' },
            { key: 'suppliers', label: 'Suppliers' },
            { key: 'email', label: 'Email Report' },
          ]}
          active={tab}
          onChange={(k) => setTab(k as TabKey)}
        />
      </div>

      <div className="p-6 max-w-[1400px]">
        {tab === 'sales' && <SalesTab window={window} />}
        {tab === 'payments' && <PaymentsTab window={window} />}
        {tab === 'taxes' && <TaxesTab window={window} />}
        {tab === 'suppliers' && <SuppliersTab window={window} />}
        {tab === 'inventory' && <InventoryTab window={window} branchId={branchId} />}
        {tab === 'email' && <EmailTab window={window} />}
      </div>
    </div>
  );
}
