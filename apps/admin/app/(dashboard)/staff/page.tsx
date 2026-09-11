'use client';

import { useCallback, useEffect, useState } from 'react';
import { branchesApi, rolesApi, staffApi } from '@/lib/pos-api';
import type { Branch, Role, Staff } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button } from '@/components/ui';
import { Modal, ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

// The Staff tab is the default screen of the Staff & Roles section — it is the
// index route, so there is no `/staff/staff` and no redirect.
export default function StaffTab() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);

  useEffect(() => {
    void rolesApi.list().then(setRoles).catch(() => setRoles([]));
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  const load = useCallback(() => staffApi.list(), []);

  return (
    <ResourcePage<Staff>
      title="Staff"
      description="Staff sign in to the console with a password and to the POS terminal with a branch-scoped PIN."
      load={load}
      create={(d) => staffApi.create(d)}
      update={(id, d) => staffApi.update(id, d)}
      remove={(id) => staffApi.deactivate(id)}
      searchKeys={['email', 'display_name', 'staff_number']}
      defaults={{ is_active: true, is_admin: false, is_driver: false }}
      emptyMessage="No staff yet. Add your first cashier."
      columns={[
        { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (s) => s.display_name ?? s.email, render: (s) => <span className="font-medium">{s.display_name ?? s.email}</span> },
        { header: 'Email', priority: 'secondary', sortable: true, sortAccessor: (s) => s.email, render: (s) => <span className="text-xs text-gray-500">{s.email}</span> },
        { header: 'Staff no.', sortable: true, sortAccessor: (s) => s.staff_number ?? null, render: (s) => s.staff_number ?? '—' },
        { header: 'Role', sortable: true, sortAccessor: (s) => s.role_name ?? null, render: (s) => s.role_name ?? <span className="text-gray-400">None</span> },
        {
          header: 'PIN',
          sortable: true,
          sortAccessor: (s) => (s.has_pin ? 'Set' : 'Not set'),
          render: (s) =>
            s.has_pin ? <Badge variant="success">Set</Badge> : <Badge variant="warning">Not set</Badge>,
        },
        { header: 'Branches', sortable: true, sortAccessor: (s) => s.branch_ids.length, render: (s) => s.branch_ids.length || '—' },
        { header: 'Status', sortable: true, sortAccessor: (s) => (s.is_active ? 'Active' : 'Inactive'), render: (s) => <StatusBadge active={s.is_active} /> },
      ]}
      fields={[
        { name: 'display_name', label: 'Full name', required: true },
        { name: 'email', label: 'Email', required: true },
        { name: 'staff_number', label: 'Staff number' },
        { name: 'phone', label: 'Phone' },
        {
          name: 'password',
          label: 'Console password',
          type: 'password',
          helper: 'Leave blank to keep the current password',
        },
        {
          name: 'pin',
          label: 'Terminal PIN',
          type: 'password',
          helper: '4–8 digits. Leave blank to keep the current PIN.',
        },
        {
          name: 'role_id',
          label: 'Role',
          type: 'select',
          options: roles.map((r) => ({ value: r.id, label: r.name })),
        },
        { name: 'is_driver', label: 'Can act as a delivery driver', type: 'checkbox' },
        { name: 'is_admin', label: 'Full console admin', type: 'checkbox' },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
      toolbar={
        <span className="text-[11px] text-gray-400 font-body">
          {branches.length} branch{branches.length === 1 ? '' : 'es'}
        </span>
      }
      rowActions={(row, reload) => <BranchAssignment staff={row} branches={branches} onSaved={reload} />}
    />
  );
}

function BranchAssignment({
  staff,
  branches,
  onSaved,
}: {
  staff: Staff;
  branches: Branch[];
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>(staff.branch_ids);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function save() {
    setSaving(true);
    setError('');
    try {
      await staffApi.update(staff.id, { branch_ids: selected });
      setOpen(false);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <button
        onClick={() => {
          setSelected(staff.branch_ids);
          setOpen(true);
        }}
        className="inline-flex items-center min-h-11 min-w-11 md:min-h-0 md:min-w-0 text-xs text-primary hover:underline font-body"
      >
        Branches
      </button>
      {open && (
        <Modal title={`Branches for ${staff.display_name ?? staff.email}`} onClose={() => setOpen(false)}>
          <p className="mb-3 text-xs text-gray-500 font-body">
            A PIN only works at branches the staff member is assigned to.
          </p>
          <div className="space-y-2">
            {branches.map((b) => (
              <label key={b.id} className="flex items-center gap-2 text-sm font-body">
                <input
                  type="checkbox"
                  checked={selected.includes(b.id)}
                  onChange={(e) =>
                    setSelected((prev) =>
                      e.target.checked ? [...prev, b.id] : prev.filter((id) => id !== b.id),
                    )
                  }
                  className="h-4 w-4 accent-[color:var(--color-primary)]"
                />
                {b.name}
              </label>
            ))}
            {branches.length === 0 && (
              <p className="text-xs text-gray-400">Create a branch first.</p>
            )}
          </div>
          {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={save} loading={saving}>
              Save
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
