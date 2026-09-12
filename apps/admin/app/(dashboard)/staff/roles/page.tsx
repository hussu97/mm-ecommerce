'use client';

import { useCallback, useEffect, useState } from 'react';
import { rolesApi } from '@/lib/pos-api';
import type { PermissionCatalogue, Role } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Spinner } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';

export default function RolesTab() {
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
    <div>
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
                className="inline-flex items-center min-h-11 min-w-11 md:min-h-0 md:min-w-0 text-xs text-primary hover:underline font-body"
              >
                Edit permissions
              </button>
              {role.user_count === 0 && (
                <button
                  onClick={async () => {
                    await rolesApi.remove(role.id).catch(() => {});
                    void reload();
                  }}
                  className="inline-flex items-center min-h-11 min-w-11 md:min-h-0 md:min-w-0 text-xs text-red-500 hover:underline font-body"
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
