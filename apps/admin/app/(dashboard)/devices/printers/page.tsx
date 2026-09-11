'use client';

import { useCallback } from 'react';
import { printersApi } from '@/lib/pos-api';
import type { Printer } from '@/lib/pos-types';
import { Badge } from '@/components/ui';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';
import { useBranchOptions } from '../_shared';

export default function PrintersTab() {
  const { branchOptions, branchName } = useBranchOptions();
  const load = useCallback(() => printersApi.list(), []);

  return (
    <ResourcePage<Printer>
      title="Printers"
      description="ESC/POS thermal printers. LAN printers are the most reliable and can be shared by several terminals."
      load={load}
      create={(d) => printersApi.create(d)}
      update={(id, d) => printersApi.update(id, d)}
      remove={(id) => printersApi.remove(id)}
      searchKeys={['name']}
      defaults={{
        role: 'receipt',
        connection: 'lan',
        port: 9100,
        paper_width_mm: 80,
        characters_per_line: 48,
        codepage: 'cp864',
        supports_arabic: true,
        cut_after_print: true,
        has_cash_drawer: false,
        copies: 1,
        is_default: false,
        is_active: true,
      }}
      emptyMessage="No printers configured."
      columns={[
        { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (p) => p.name, render: (p) => <span className="font-medium">{p.name}</span> },
        { header: 'Branch', sortable: true, sortAccessor: (p) => branchName(p.branch_id), render: (p) => branchName(p.branch_id) },
        { header: 'Role', sortable: true, sortAccessor: (p) => p.role, render: (p) => <span className="capitalize">{p.role}</span> },
        {
          header: 'Connection',
          sortable: true,
          sortAccessor: (p) => p.connection,
          render: (p) =>
            p.connection === 'lan' ? (
              <span className="text-xs">
                {p.ip_address ?? '—'}:{p.port}
              </span>
            ) : (
              <span className="capitalize text-xs">{p.connection}</span>
            ),
        },
        { header: 'Width', sortable: true, sortAccessor: (p) => p.paper_width_mm, render: (p) => `${p.paper_width_mm}mm / ${p.characters_per_line} cols` },
        {
          header: 'Drawer',
          sortable: true,
          sortAccessor: (p) => (p.has_cash_drawer ? 'Yes' : '—'),
          render: (p) => (p.has_cash_drawer ? <Badge variant="info">Yes</Badge> : '—'),
        },
        { header: 'Default', sortable: true, sortAccessor: (p) => (p.is_default ? 'Yes' : '—'), render: (p) => (p.is_default ? 'Yes' : '—') },
        { header: 'Status', sortable: true, sortAccessor: (p) => (p.is_active && !p.deleted_at ? 'Active' : 'Inactive'), render: (p) => <StatusBadge active={p.is_active && !p.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'branch_id', label: 'Branch', type: 'select', required: true, options: branchOptions },
        {
          name: 'role',
          label: 'Role',
          type: 'select',
          options: [
            { value: 'receipt', label: 'Receipt' },
            { value: 'kitchen', label: 'Kitchen' },
            { value: 'label', label: 'Label' },
            { value: 'report', label: 'Reports' },
          ],
        },
        {
          name: 'connection',
          label: 'Connection',
          type: 'select',
          options: [
            { value: 'lan', label: 'LAN (TCP 9100)' },
            { value: 'bluetooth', label: 'Bluetooth / MFi' },
            { value: 'usb', label: 'USB' },
            { value: 'airprint', label: 'AirPrint' },
          ],
        },
        { name: 'ip_address', label: 'IP address', placeholder: '192.168.1.50' },
        { name: 'port', label: 'Port', type: 'number' },
        { name: 'paper_width_mm', label: 'Paper width (mm)', type: 'number' },
        {
          name: 'characters_per_line',
          label: 'Characters per line',
          type: 'number',
          helper: '48 for 80mm, 32 for 58mm',
        },
        {
          name: 'codepage',
          label: 'Code page',
          helper: 'cp864 for Arabic, cp437 for Latin only',
        },
        { name: 'supports_arabic', label: 'Supports Arabic', type: 'checkbox' },
        { name: 'cut_after_print', label: 'Cut after printing', type: 'checkbox' },
        {
          name: 'has_cash_drawer',
          label: 'Cash drawer attached',
          type: 'checkbox',
          helper: 'The drawer is wired to this printer',
        },
        { name: 'copies', label: 'Copies', type: 'number' },
        { name: 'is_default', label: 'Default for this role', type: 'checkbox' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
