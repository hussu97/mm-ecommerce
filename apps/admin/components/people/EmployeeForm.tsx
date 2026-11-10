'use client';

import { useState } from 'react';
import { employeesApi, type Employee, type EmployeeCreate } from '@/lib/people-api';
import type { Branch, LegalEntity } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Button, Input, Select, Textarea } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';

/** Create an employee, or edit one's profile. Documents are added on the
 *  employee's page, where a scan can fill the rest of the profile. */

type Draft = {
  full_name: string;
  full_name_ar: string;
  nationality: string;
  date_of_birth: string;
  gender: '' | 'M' | 'F';
  job_title: string;
  personal_number: string;
  phone: string;
  legal_entity_id: string;
  branch_id: string;
  joined_on: string;
  left_on: string;
  status: 'active' | 'left';
  notes: string;
};

function draftFrom(e: Employee | null): Draft {
  return {
    full_name: e?.full_name ?? '',
    full_name_ar: e?.full_name_ar ?? '',
    nationality: e?.nationality ?? '',
    date_of_birth: e?.date_of_birth ?? '',
    gender: (e?.gender as Draft['gender']) ?? '',
    job_title: e?.job_title ?? '',
    personal_number: e?.personal_number ?? '',
    phone: e?.phone ?? '',
    legal_entity_id: e?.legal_entity_id ?? '',
    branch_id: e?.branch_id ?? '',
    joined_on: e?.joined_on ?? '',
    left_on: e?.left_on ?? '',
    status: (e?.status as Draft['status']) ?? 'active',
    notes: e?.notes ?? '',
  };
}

export function EmployeeForm({
  employee,
  entities,
  branches,
  onClose,
  onSaved,
}: {
  employee?: Employee | null;
  entities: LegalEntity[];
  branches: Branch[];
  onClose: () => void;
  onSaved: (saved: Employee) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(employee ?? null));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function save() {
    if (!draft.full_name.trim()) {
      setError('The name is required.');
      return;
    }
    setSaving(true);
    setError('');
    const blank = (v: string) => v.trim() || null;
    const body: EmployeeCreate = {
      full_name: draft.full_name.trim(),
      full_name_ar: blank(draft.full_name_ar),
      nationality: blank(draft.nationality),
      date_of_birth: draft.date_of_birth || null,
      gender: draft.gender || null,
      job_title: blank(draft.job_title),
      personal_number: blank(draft.personal_number),
      phone: blank(draft.phone),
      legal_entity_id: draft.legal_entity_id || null,
      branch_id: draft.branch_id || null,
      joined_on: draft.joined_on || null,
      left_on: draft.left_on || null,
      status: draft.status,
      notes: blank(draft.notes),
    };
    try {
      const saved = employee
        ? await employeesApi.update(employee.id, body)
        : await employeesApi.create(body);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the employee.');
      setSaving(false);
    }
  }

  return (
    <Modal title={employee ? 'Edit employee' : 'New employee'} onClose={onClose} wide>
      <div className="space-y-4 font-body text-sm">
        {!employee && (
          <p className="text-xs text-gray-500">
            Start with the name — then add the passport, visa, labour card and health card on the
            employee&rsquo;s page, where each scan fills the rest of this profile.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Input label="Full name *" value={draft.full_name} onChange={(e) => set('full_name', e.target.value)} />
          <Input
            label="Name in Arabic"
            dir="rtl"
            value={draft.full_name_ar}
            onChange={(e) => set('full_name_ar', e.target.value)}
          />
          <Input label="Job title" value={draft.job_title} onChange={(e) => set('job_title', e.target.value)} />
          <Input label="Nationality" value={draft.nationality} onChange={(e) => set('nationality', e.target.value)} />
          <Input
            label="Date of birth"
            type="date"
            value={draft.date_of_birth}
            onChange={(e) => set('date_of_birth', e.target.value)}
          />
          <Select
            label="Sex"
            value={draft.gender}
            onChange={(e) => set('gender', e.target.value as Draft['gender'])}
            placeholder="—"
            options={[
              { value: 'M', label: 'Male' },
              { value: 'F', label: 'Female' },
            ]}
          />
          <Input
            label="Personal number (MOHRE)"
            value={draft.personal_number}
            onChange={(e) => set('personal_number', e.target.value)}
          />
          <Input label="Phone" value={draft.phone} onChange={(e) => set('phone', e.target.value)} />
          <Select
            label="Sponsor (legal entity)"
            value={draft.legal_entity_id}
            onChange={(e) => set('legal_entity_id', e.target.value)}
            placeholder="—"
            options={entities.map((e) => ({ value: e.id, label: e.legal_name }))}
          />
          <Select
            label="Branch"
            value={draft.branch_id}
            onChange={(e) => set('branch_id', e.target.value)}
            placeholder="—"
            options={branches.map((b) => ({ value: b.id, label: b.name }))}
          />
          <Input label="Joined on" type="date" value={draft.joined_on} onChange={(e) => set('joined_on', e.target.value)} />
          {employee && (
            <>
              <Select
                label="Status"
                value={draft.status}
                onChange={(e) => set('status', e.target.value as Draft['status'])}
                options={[
                  { value: 'active', label: 'Active' },
                  { value: 'left', label: 'Left (no expiry warnings)' },
                ]}
              />
              <Input label="Left on" type="date" value={draft.left_on} onChange={(e) => set('left_on', e.target.value)} />
            </>
          )}
        </div>
        <Textarea label="Notes" rows={2} value={draft.notes} onChange={(e) => set('notes', e.target.value)} />
        {error && <p className="text-xs text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void save()} loading={saving}>
            {employee ? 'Save' : 'Create'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
