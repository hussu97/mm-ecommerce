'use client';

import { useCallback, useEffect, useState } from 'react';
import { branchesApi, devicesApi, printersApi } from '@/lib/pos-api';
import type { Branch, Device, Printer } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { formatAge } from '@/lib/utils';
import { Badge, Button, TabBar } from '@/components/ui';
import { Modal, ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

/** How each platform spells itself. `text-transform` cannot produce "iOS". */
const PLATFORM_LABELS: Record<string, string> = {
  ios: 'iOS',
  android: 'Android',
};

export default function DevicesPage() {
  const [tab, setTab] = useState<'devices' | 'printers'>('devices');
  const [branches, setBranches] = useState<Branch[]>([]);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  const branchOptions = branches.map((b) => ({ value: b.id, label: b.name }));
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? '—';

  return (
    <div>
      <div className="border-b border-gray-200 px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-3">Devices &amp; Printers</h1>
        <TabBar
          tabs={[
            { key: 'devices', label: 'Terminals' },
            { key: 'printers', label: 'Printers' },
          ]}
          active={tab}
          onChange={(k) => setTab(k as 'devices' | 'printers')}
        />
      </div>
      {tab === 'devices' ? (
        <DevicesTab branchOptions={branchOptions} branchName={branchName} />
      ) : (
        <PrintersTab branchOptions={branchOptions} branchName={branchName} />
      )}
    </div>
  );
}

function DevicesTab({
  branchOptions,
  branchName,
}: {
  branchOptions: Array<{ value: string; label: string }>;
  branchName: (id: string) => string;
}) {
  const load = useCallback(() => devicesApi.list(), []);

  return (
    <ResourcePage<Device>
      title="Terminals"
      description="Register an iPad here, then pair it from the app using a one-time code."
      load={load}
      create={(d) => devicesApi.create(d)}
      update={(id, d) => devicesApi.update(id, d)}
      remove={(id) => devicesApi.remove(id)}
      searchKeys={['name', 'reference']}
      defaults={{ type: 'cashier' }}
      emptyMessage="No terminals registered yet."
      columns={[
        { header: 'Name', priority: 'primary', render: (d) => <span className="font-medium">{d.name}</span> },
        { header: 'Reference', priority: 'secondary', render: (d) => <code className="text-xs text-gray-500">{d.reference}</code> },
        { header: 'Type', render: (d) => <span className="capitalize">{d.type.replace('_', ' ')}</span> },
        { header: 'Branch', render: (d) => branchName(d.branch_id) },
        {
          header: 'Pairing',
          render: (d) =>
            d.status === 'used' ? (
              <Badge variant="success">Paired</Badge>
            ) : d.pairing_code ? (
              <code className="text-xs font-bold tracking-widest text-primary">{d.pairing_code}</code>
            ) : (
              <Badge variant="neutral">Unpaired</Badge>
            ),
        },
        {
          header: 'Last seen',
          render: (d) =>
            d.last_seen_at ? (
              <span className="text-xs text-gray-500">
                {new Date(d.last_seen_at).toLocaleString()}
              </span>
            ) : (
              '—'
            ),
        },
        {
          header: 'Online orders',
          render: (d) =>
            d.auto_accept_online_orders ? (
              <Badge variant="info">Auto-accept</Badge>
            ) : (
              <span className="text-xs text-gray-500">Needs accepting</span>
            ),
        },
        {
          // Version, build and platform in one cell rather than three columns:
          // the table already has seven, and `DataTable` turns each column into
          // a labelled line on a phone.
          //
          // The age underneath is the part that matters. This used to be the
          // version captured at pairing and never updated, so it read as
          // current and could be thirty builds out of date. It is now refreshed
          // on every request the terminal makes — which means it is exactly as
          // fresh as the last time the terminal spoke, and saying so is the
          // difference between a number you can act on and one you cannot.
          header: 'App',
          render: (d) => {
            if (!d.app_version && !d.build_number) {
              return (
                <span className="text-xs text-gray-400">
                  {d.status === 'used' ? 'not reported yet' : '—'}
                </span>
              );
            }
            return (
              <div className="leading-tight">
                <div className="text-xs text-gray-700">
                  {d.app_version ?? '—'}
                  {d.build_number && <span className="text-gray-500"> ({d.build_number})</span>}
                  {/* The label is mapped, not CSS-transformed: `uppercase`
                      gives "IOS" and `capitalize` gives it too, because both
                      only touch the first letter or all of them. */}
                  {d.platform && (
                    <span className="ml-1.5 text-gray-400">
                      {PLATFORM_LABELS[d.platform] ?? d.platform}
                    </span>
                  )}
                </div>
                {d.last_seen_at && (
                  <div className="text-[11px] text-gray-400">
                    seen {formatAge(d.last_seen_at)} ago
                  </div>
                )}
              </div>
            );
          },
        },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'reference', label: 'Reference', required: true, helper: 'Short unique code, e.g. C01' },
        {
          name: 'type',
          label: 'Type',
          type: 'select',
          required: true,
          options: [
            { value: 'cashier', label: 'Cashier' },
            { value: 'sub_cashier', label: 'Sub-cashier' },
            { value: 'display', label: 'Customer display' },
            { value: 'notifier', label: 'Order-ready screen' },
          ],
        },
        { name: 'branch_id', label: 'Branch', type: 'select', required: true, options: branchOptions },
        {
          name: 'auto_accept_online_orders',
          label: 'Auto-accept website orders',
          type: 'checkbox',
          helper:
            'Takes website orders and prints them without anyone pressing Accept. '
            + 'For a kitchen with nobody watching the iPad — leave off for a counter terminal.',
        },
      ]}
      rowActions={(row, reload) => <PairingActions device={row} onDone={reload} />}
    />
  );
}

function PairingActions({ device, onDone }: { device: Device; onDone: () => void }) {
  const [code, setCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function issue() {
    setBusy(true);
    setError('');
    try {
      const updated = await devicesApi.issuePairingCode(device.id);
      setCode(updated.pairing_code);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to issue a code.');
    } finally {
      setBusy(false);
    }
  }

  async function unpair() {
    setBusy(true);
    try {
      await devicesApi.unpair(device.id);
      onDone();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        onClick={issue}
        disabled={busy}
        className="inline-flex items-center min-h-11 min-w-11 md:min-h-0 md:min-w-0 text-xs text-primary hover:underline font-body disabled:opacity-50"
      >
        Pair
      </button>
      {device.status === 'used' && (
        <button
          onClick={unpair}
          disabled={busy}
          className="inline-flex items-center min-h-11 min-w-11 md:min-h-0 md:min-w-0 text-xs text-gray-500 hover:underline font-body disabled:opacity-50"
        >
          Unpair
        </button>
      )}
      {(code || error) && (
        <Modal
          title={error ? 'Pairing failed' : 'Pairing code'}
          onClose={() => {
            setCode(null);
            setError('');
          }}
        >
          {error ? (
            <p className="text-sm text-red-600 font-body">{error}</p>
          ) : (
            <>
              <p className="mb-4 text-sm text-gray-600 font-body">
                Enter this code in the MM POS app on {device.name}. It expires in 15 minutes and
                can only be used once.
              </p>
              <p className="text-center font-display text-4xl tracking-[0.3em] text-primary">
                {code}
              </p>
            </>
          )}
        </Modal>
      )}
    </>
  );
}

function PrintersTab({
  branchOptions,
  branchName,
}: {
  branchOptions: Array<{ value: string; label: string }>;
  branchName: (id: string) => string;
}) {
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
        { header: 'Name', priority: 'primary', render: (p) => <span className="font-medium">{p.name}</span> },
        { header: 'Branch', render: (p) => branchName(p.branch_id) },
        { header: 'Role', render: (p) => <span className="capitalize">{p.role}</span> },
        {
          header: 'Connection',
          render: (p) =>
            p.connection === 'lan' ? (
              <span className="text-xs">
                {p.ip_address ?? '—'}:{p.port}
              </span>
            ) : (
              <span className="capitalize text-xs">{p.connection}</span>
            ),
        },
        { header: 'Width', render: (p) => `${p.paper_width_mm}mm / ${p.characters_per_line} cols` },
        {
          header: 'Drawer',
          render: (p) => (p.has_cash_drawer ? <Badge variant="info">Yes</Badge> : '—'),
        },
        { header: 'Default', render: (p) => (p.is_default ? 'Yes' : '—') },
        { header: 'Status', render: (p) => <StatusBadge active={p.is_active && !p.deleted_at} /> },
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
