'use client';

import { useCallback, useEffect, useState } from 'react';
import { branchesApi, rolesApi, staffApi } from '@/lib/pos-api';
import type { Branch, PermissionCatalogue, Role, Staff } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Spinner, TabBar } from '@/components/ui';
import { Modal, ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';

export default function StaffPage() {
  const [tab, setTab] = useState<'staff' | 'roles'>('staff');

  return (
    <div>
      <div className="border-b border-gray-200 px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-3">Staff &amp; Roles</h1>
        <TabBar
          tabs={[
            { key: 'staff', label: 'Staff' },
            { key: 'roles', label: 'Roles' },
          ]}
          active={tab}
          onChange={(k) => setTab(k as 'staff' | 'roles')}
        />
      </div>
      {tab === 'staff' ? <StaffTab /> : <RolesTab />}
    </div>
  );
}

// ─── Staff ────────────────────────────────────────────────────────────────────

function StaffTab() {
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
        { header: 'Name', render: (s) => <span className="font-medium">{s.display_name ?? s.email}</span> },
        { header: 'Email', render: (s) => <span className="text-xs text-gray-500">{s.email}</span> },
        { header: 'Staff no.', render: (s) => s.staff_number ?? '—' },
        { header: 'Role', render: (s) => s.role_name ?? <span className="text-gray-400">None</span> },
        {
          header: 'PIN',
          render: (s) =>
            s.has_pin ? <Badge variant="success">Set</Badge> : <Badge variant="warning">Not set</Badge>,
        },
        { header: 'Branches', render: (s) => s.branch_ids.length || '—' },
        { header: 'Status', render: (s) => <StatusBadge active={s.is_active} /> },
      ]}
      fields={[
        { name: 'display_name', label: 'Full name', required: true },
        { name: 'email', label: 'Email', required: true },
        { name: 'staff_number', label: 'Staff number' },
        { name: 'phone', label: 'Phone' },
        {
          name: 'password',
          label: 'Console password',
          helper: 'Leave blank to keep the current password',
        },
        {
          name: 'pin',
          label: 'Terminal PIN',
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
        className="text-xs text-primary hover:underline font-body"
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

// ─── Roles ────────────────────────────────────────────────────────────────────

function RolesTab() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [catalogue, setCatalogue] = useState<PermissionCatalogue | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<Role | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [r, c] = await Promise.all([rolesApi.list(), rolesApi.permissions()]);
      setRoles(r);
      setCatalogue(c);
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load roles.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="p-6 max-w-[1400px]">
      {error && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}
      <div className="mb-4 flex justify-end">
        <Button onClick={() => setCreating(true)}>New Role</Button>
      </div>

      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {roles.map((role) => (
          <div key={role.id} className="rounded border border-gray-200 bg-white p-4">
            <div className="mb-2 flex items-start justify-between">
              <div>
                <h3 className="font-display text-sm text-primary">{role.name}</h3>
                <p className="text-[11px] text-gray-400 font-body">
                  {role.user_count} user{role.user_count === 1 ? '' : 's'}
                </p>
              </div>
              {role.is_super_admin && <Badge variant="info">Super admin</Badge>}
            </div>
            <p className="text-xs text-gray-500 font-body">
              {role.is_super_admin
                ? 'Every permission, always'
                : `${role.permissions.length} permission${role.permissions.length === 1 ? '' : 's'}`}
            </p>
            <div className="mt-3 flex gap-3">
              <button
                onClick={() => setEditing(role)}
                className="text-xs text-primary hover:underline font-body"
              >
                Edit permissions
              </button>
              {role.user_count === 0 && (
                <button
                  onClick={async () => {
                    await rolesApi.remove(role.id).catch(() => {});
                    void reload();
                  }}
                  className="text-xs text-red-500 hover:underline font-body"
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
        {roles.length === 0 && (
          <p className="text-sm text-gray-400 font-body">No roles yet.</p>
        )}
      </div>

      {(editing || creating) && catalogue && (
        <RoleEditor
          role={editing}
          catalogue={catalogue}
          onClose={() => {
            setEditing(null);
            setCreating(false);
          }}
          onSaved={() => {
            setEditing(null);
            setCreating(false);
            void reload();
          }}
        />
      )}
    </div>
  );
}

function RoleEditor({
  role,
  catalogue,
  onClose,
  onSaved,
}: {
  role: Role | null;
  catalogue: PermissionCatalogue;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(role?.name ?? '');
  const [permissions, setPermissions] = useState<string[]>(role?.permissions ?? []);
  const [superAdmin, setSuperAdmin] = useState(role?.is_super_admin ?? false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  function toggle(slug: string) {
    setPermissions((prev) =>
      prev.includes(slug) ? prev.filter((p) => p !== slug) : [...prev, slug],
    );
  }

  function toggleGroup(slugs: string[], on: boolean) {
    setPermissions((prev) =>
      on ? Array.from(new Set([...prev, ...slugs])) : prev.filter((p) => !slugs.includes(p)),
    );
  }

  async function save() {
    if (!name.trim()) {
      setError('Name required');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const payload = { name, permissions, is_super_admin: superAdmin };
      if (role) await rolesApi.update(role.id, payload);
      else await rolesApi.create(payload);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={role ? `Edit ${role.name}` : 'New role'} onClose={onClose} wide>
      <div className="space-y-4">
        <Input label="Role name" value={name} onChange={(e) => setName(e.target.value)} />

        <label className="flex items-center gap-2 text-sm font-body">
          <input
            type="checkbox"
            checked={superAdmin}
            onChange={(e) => setSuperAdmin(e.target.checked)}
            className="h-4 w-4 accent-[color:var(--color-primary)]"
          />
          Super admin — grants every permission, including future ones
        </label>

        {!superAdmin &&
          Object.entries(catalogue.groups).map(([group, entries]) => {
            const slugs = entries.map((e) => e.slug);
            const allOn = slugs.every((s) => permissions.includes(s));
            return (
              <section key={group} className="rounded border border-gray-200">
                <header className="flex items-center justify-between bg-gray-50 px-3 py-2">
                  <h4 className="text-xs uppercase tracking-widest text-gray-600 font-body">
                    {group}
                  </h4>
                  <button
                    onClick={() => toggleGroup(slugs, !allOn)}
                    className="text-[11px] text-primary hover:underline font-body"
                  >
                    {allOn ? 'Clear all' : 'Select all'}
                  </button>
                </header>
                <div className="grid gap-1 p-3 sm:grid-cols-2">
                  {entries.map((entry) => (
                    <label
                      key={entry.slug}
                      className="flex items-start gap-2 text-xs font-body text-gray-700"
                      title={entry.description}
                    >
                      <input
                        type="checkbox"
                        checked={permissions.includes(entry.slug)}
                        onChange={() => toggle(entry.slug)}
                        className="mt-0.5 h-3.5 w-3.5 accent-[color:var(--color-primary)]"
                      />
                      <span>{entry.description}</span>
                    </label>
                  ))}
                </div>
              </section>
            );
          })}

        {error && <p className="text-xs text-red-600 font-body">{error}</p>}
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={save} loading={saving}>
          Save
        </Button>
      </div>
    </Modal>
  );
}
