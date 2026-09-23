'use client';

import { useCallback, useEffect, useState } from 'react';
import type { Schemas } from '@mm/types';
import { branchesApi, devicesApi } from '@/lib/pos-api';
import type { Branch, Device } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';
import { BranchWeeklyHours } from '@/components/pos/BranchWeeklyHours';
import { BranchHolidays } from '@/components/pos/BranchHolidays';
import { BranchChannelTaxConfigs } from '@/components/pos/BranchChannelTaxConfigs';

/** The local-first counter rollout flag (`branches.counter_local_first`). */
type CounterRollout = 'off' | 'shadow' | 'on';
type BranchRow = Branch & { counter_local_first?: CounterRollout };

/** A terminal as the rollout warning reads it; `supports_local_first` is decided
 *  server-side against `COUNTER_LOCAL_FIRST_MIN_BUILD`. */
type Terminal = Device &
  Partial<Pick<Schemas['DeviceResponse'], 'supports_local_first' | 'counter_mode'>>;

const ROLLOUT_LABEL: Record<CounterRollout, string> = {
  off: 'Off',
  shadow: 'Shadow',
  on: 'On',
};

/** Till terminals in service at `branchId` whose build cannot run local-first. */
function oldTerminalsAt(terminals: Terminal[], branchId: string): Terminal[] {
  return terminals.filter(
    d =>
      d.branch_id === branchId &&
      !d.deleted_at &&
      d.status !== 'disabled' &&
      (d.type === 'cashier' || d.type === 'sub_cashier') &&
      d.supports_local_first === false,
  );
}

/**
 * Where local-first checkout is switched on, and which terminals there are too
 * old to use it. Those terminals keep working — online-only — so this is a
 * warning, not a block: the branch simply is not fully local-first until they
 * update.
 */
function CounterRolloutPanel({ branches, terminals }: { branches: BranchRow[]; terminals: Terminal[] }) {
  const live = branches.filter(b => !b.deleted_at && (b.counter_local_first ?? 'off') !== 'off');
  if (live.length === 0) return null;
  return (
    <div className="border border-gray-200 p-4">
      <h2 className="font-display text-base text-primary mb-1">Local-first counter</h2>
      <p className="text-xs text-gray-500 font-body mb-3">
        Branches whose tills price and print counter sales on the iPad and sync afterwards. Set a
        branch back to Off to put every till there online-only again within a minute.
      </p>
      <ul className="space-y-2">
        {live.map(b => {
          const old = oldTerminalsAt(terminals, b.id);
          return (
            <li key={b.id} className="text-sm font-body">
              <span className="font-medium">{b.name}</span>{' '}
              <span className="text-gray-500">— {ROLLOUT_LABEL[b.counter_local_first ?? 'off']}</span>
              {old.length > 0 && (
                <p className="mt-0.5 text-xs text-amber-700">
                  {old.length} terminal{old.length === 1 ? '' : 's'} below the minimum app build stay
                  online-only until updated: {old.map(d => `${d.name} (${d.build_number ?? 'build unknown'})`).join(', ')}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function BranchesPage() {
  const load = useCallback(() => branchesApi.list() as Promise<BranchRow[]>, []);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  useEffect(() => {
    void devicesApi.list().then(setTerminals).catch(() => setTerminals([]));
  }, []);
  // The branch list drives the "returns go to" picker; loaded once.
  const [allBranches, setAllBranches] = useState<BranchRow[]>([]);
  useEffect(() => {
    void branchesApi.list().then(setAllBranches).catch(() => setAllBranches([]));
  }, []);
  // An empty return-branch select means "no return branch", which the API wants
  // as null, not "".
  const normalise = (d: Partial<BranchRow>): Partial<BranchRow> => ({
    ...d,
    return_branch_id: d.return_branch_id ? d.return_branch_id : null,
  });

  return (
    <>
    <ResourcePage<BranchRow>
      title="Branches"
      description="Shops, production kitchens and warehouses. Every order, till and stock level belongs to one."
      load={load}
      create={(d) => branchesApi.create(normalise(d as Partial<BranchRow>)) as Promise<BranchRow>}
      update={(id, d) =>
        branchesApi.update(id, normalise(d as Partial<BranchRow>)).then((b) => {
          void branchesApi.list().then(setAllBranches).catch(() => undefined);
          return b as BranchRow;
        })
      }
      remove={(id) => branchesApi.remove(id)}
      searchKeys={['name', 'reference']}
      emptyMessage="No branches yet. Create one to start using the POS."
      defaults={{
        type: 'restaurant',
        business_day_start: '04:00',
        receives_online_orders: true,
        offers_pickup: false,
        cash_enabled: true,
        uses_pos: true,
        show_recipes: false,
        counter_local_first: 'off',
        accepts_reservations: false,
        is_active: true,
        display_order: 0,
      }}
      columns={[
        { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (b) => b.name, render: (b) => <span className="font-medium">{b.name}</span> },
        { header: 'Reference', priority: 'secondary', sortable: true, sortAccessor: (b) => b.reference, render: (b) => <code className="text-xs text-gray-500">{b.reference}</code> },
        { header: 'Type', sortable: true, sortAccessor: (b) => b.type, render: (b) => <span className="capitalize">{b.type}</span> },
        { header: 'Day starts', sortable: true, sortAccessor: (b) => b.business_day_start, render: (b) => b.business_day_start },
        { header: 'Online', sortable: true, sortAccessor: (b) => (b.receives_online_orders ? 'Yes' : 'No'), render: (b) => (b.receives_online_orders ? 'Yes' : 'No') },
        { header: 'Collection', sortable: true, sortAccessor: (b) => (b.offers_pickup ? 'Yes' : 'No'), render: (b) => (b.offers_pickup ? 'Yes' : 'No') },
        {
          header: 'noon Send',
          sortable: true,
          sortAccessor: (b) => b.noon_send_outlet_code ?? null,
          render: (b) =>
            b.noon_send_outlet_code ? (
              <code className="text-xs text-gray-500">{b.noon_send_outlet_code}</code>
            ) : (
              <span className="text-xs text-gray-400">—</span>
            ),
        },
        {
          header: 'Local-first',
          sortable: true,
          sortAccessor: (b) => b.counter_local_first ?? 'off',
          render: (b) => {
            const mode = b.counter_local_first ?? 'off';
            const old = mode === 'off' ? [] : oldTerminalsAt(terminals, b.id);
            return (
              <span className="text-xs">
                {ROLLOUT_LABEL[mode]}
                {old.length > 0 && (
                  <span className="ml-1 text-amber-700" title="Terminals below the minimum build stay online-only">
                    · {old.length} old
                  </span>
                )}
              </span>
            );
          },
        },
        { header: 'Status', sortable: true, sortAccessor: (b) => (b.is_active && !b.deleted_at ? 'Active' : 'Inactive'), render: (b) => <StatusBadge active={b.is_active && !b.deleted_at} /> },
      ]}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'name_localized', label: 'Name (Arabic)' },
        {
          name: 'reference',
          label: 'Reference',
          required: true,
          helper: 'Short unique code, printed on order numbers',
        },
        {
          name: 'type',
          label: 'Type',
          type: 'select',
          options: [
            { value: 'restaurant', label: 'Restaurant / shop' },
            { value: 'kitchen', label: 'Production kitchen' },
            { value: 'warehouse', label: 'Warehouse' },
          ],
        },
        { name: 'phone', label: 'Phone' },
        { name: 'address', label: 'Address', type: 'textarea' },
        { name: 'address_localized', label: 'Address (Arabic)', type: 'textarea' },
        {
          name: 'city',
          label: 'City',
          placeholder: 'Sharjah',
          helper: 'Shown to customers choosing where to collect from',
        },
        { name: 'city_localized', label: 'City (Arabic)', placeholder: 'الشارقة' },
        {
          name: 'business_day_start',
          label: 'Trading day starts',
          placeholder: '04:00',
          helper: 'Sales before this time count toward the previous day',
        },
        { name: 'tax_number', label: 'Tax registration number (TRN)' },
        { name: 'tax_registration_name', label: 'Tax registration name' },
        { name: 'receipt_header', label: 'Receipt header', type: 'textarea' },
        { name: 'receipt_footer', label: 'Receipt footer', type: 'textarea' },
        {
          name: 'noon_send_outlet_code',
          label: 'noon Send outlet code',
          placeholder: 'PCKP_... or CMFRTF2DXS',
          helper:
            'From registering this branch with noon Send. Leave empty and its orders go by Lalamove instead.',
        },
        {
          name: 'noon_send_outlet_address_code',
          label: 'noon Send outlet address code',
          placeholder: 'addr::restaurant_outlet::ae::CODE::2',
          helper:
            'Optional. Pins the outlet address revision. The trailing number changes whenever noon edits the address, so leave it empty rather than let it go stale.',
        },
        { name: 'receives_online_orders', label: 'Accepts online orders', type: 'checkbox' },
        {
          name: 'offers_pickup',
          label: 'Customers can collect from here',
          type: 'checkbox',
          helper:
            'Puts this branch on the checkout as a collection point. Needs a pin, an address and a city — a kitchen that bakes online orders is not automatically somewhere to send a customer.',
        },
        {
          name: 'cash_enabled',
          label: 'Handles cash (has a till drawer)',
          type: 'checkbox',
          helper:
            'Turn off for a cashless kitchen — the POS then skips the opening float and end-of-shift cash count.',
        },
        {
          name: 'uses_pos',
          label: 'Runs the POS register',
          type: 'checkbox',
          helper:
            'Turn off for a branch with no till (e.g. DSO, Karama). A transfer or return sent to it is auto-received on its behalf, and it gets no "to receive" notification.',
        },
        {
          name: 'show_recipes',
          label: 'Show the Recipes tab on the POS',
          type: 'checkbox',
          helper:
            'Adds a read-only Recipes tab to every terminal at this branch — the shop-floor reference for how made items are built from their ingredients. View-only; recipes are edited in the Recipes console.',
        },
        {
          name: 'counter_local_first',
          label: 'Local-first counter',
          type: 'select',
          options: [
            { value: 'off', label: 'Off — every till rings up through the server' },
            { value: 'shadow', label: 'Shadow — server stays live, tills also price locally and report differences' },
            { value: 'on', label: 'On — tills price, print and sync counter sales themselves' },
          ],
          helper:
            'The rollout switch for local-first checkout. Only terminals on a new enough app build use it; older ones stay online-only whatever this says. Back to Off is the kill switch: every till is online-only again within a minute.',
        },
        {
          name: 'return_branch_id',
          label: 'Returns go to',
          type: 'select',
          options: [
            { value: '', label: 'No return branch' },
            ...allBranches
              .filter((b) => !b.deleted_at)
              .map((b) => ({ value: b.id, label: b.name })),
          ],
          helper:
            'Where this branch sends surplus, expired or damaged stock. The till uses this automatically — staff never pick a destination for a return.',
        },
        { name: 'accepts_reservations', label: 'Accepts reservations', type: 'checkbox' },
        { name: 'display_order', label: 'Display order', type: 'number' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />

    <div className="mt-8">
      <CounterRolloutPanel branches={allBranches} terminals={terminals} />
    </div>

    {/* The weekly schedule — the single source of truth for when the branch
        trades. Its own section rather than fields on the branch form, and it
        sits with the holidays below because both answer "when is this branch
        open". */}
    <div className="mt-8">
      <BranchWeeklyHours />
    </div>

    {/* Its own section rather than a field on the branch, because a closure is
        a row per date and a branch has none most of the time. It sits here
        rather than under Delivery because it belongs with the trading hours
        above — same question, answered for a whole day — even though what
        reads it is the delivery estimate. */}
    <div className="mt-8">
      <BranchHolidays />
    </div>

    {/* Per-channel VAT + trade-license identity. Its own section because a
        branch can trade under more than one license depending on the sales
        channel — Barsha's counter is not VAT-registered while its website and
        aggregator sales are. */}
    <div className="mt-8">
      <BranchChannelTaxConfigs />
    </div>
    </>
  );
}
