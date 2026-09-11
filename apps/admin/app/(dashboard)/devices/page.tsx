'use client';

import { useCallback, useState } from 'react';
import { devicesApi } from '@/lib/pos-api';
import type { Device } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { formatAge } from '@/lib/utils';
import { Badge } from '@/components/ui';
import { Modal, ResourcePage } from '@/components/pos/ResourcePage';
import { useBranchOptions } from './_shared';

/** How each platform spells itself. `text-transform` cannot produce "iOS". */
const PLATFORM_LABELS: Record<string, string> = {
  ios: 'iOS',
  android: 'Android',
};

// The Terminals tab is the default screen of the Devices & Printers section —
// it is the index route, so there is no `/devices/devices` and no redirect.
export default function DevicesTab() {
  const { branchOptions, branchName } = useBranchOptions();
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
          // The age underneath is the part that matters, and it is why the
          // separate "Last seen" column is gone: that showed the same instant
          // as an absolute timestamp — "8/21/2026, 2:51:13 PM" — which makes
          // the reader do the subtraction to answer the only question they had.
          // One fact, in the place it qualifies.
          //
          // Because the version used to be captured at pairing and never
          // updated, it read as current while being potentially thirty builds
          // stale. It is now refreshed on every request the terminal makes,
          // which means it is exactly as fresh as the last time that terminal
          // spoke — and saying so is the difference between a number somebody
          // can act on and one they cannot.
          header: 'App',
          render: (d) => {
            const version = d.app_version || d.build_number ? (
              <div className="text-xs text-gray-700">
                {d.app_version ?? '—'}
                {d.build_number && <span className="text-gray-500"> ({d.build_number})</span>}
                {/* The label is mapped, not CSS-transformed: `uppercase` gives
                    "IOS" and `capitalize` gives it too, because both only touch
                    the first letter or all of them. */}
                {d.platform && (
                  <span className="ml-1.5 text-gray-400">
                    {PLATFORM_LABELS[d.platform] ?? d.platform}
                  </span>
                )}
              </div>
            ) : (
              // Paired but silent: every terminal looks like this until the
              // build that sends the headers reaches it. Distinguished from a
              // terminal that has never paired, which has nothing to report.
              <div className="text-xs text-gray-400">
                {d.status === 'used' ? 'not reported yet' : '—'}
              </div>
            );

            return (
              <div className="leading-tight">
                {version}
                {/* Unconditional, not tied to the version above. Dropping the
                    "Last seen" column means this line is now the only place the
                    console says when a terminal last spoke, and a till that has
                    not reported a build yet is exactly the one somebody needs
                    that for. */}
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
