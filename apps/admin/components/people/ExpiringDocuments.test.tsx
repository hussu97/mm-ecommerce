import { describe, expect, it } from 'vitest';
import type { ExpiringDocument } from '@/lib/people-api';
import { groupByOwner, ownerHref, renewalHref } from './ExpiringDocuments';

function item(extra: Partial<ExpiringDocument>): ExpiringDocument {
  return {
    owner_kind: 'employee',
    owner_id: 'e1',
    owner_name: 'Juan Cruz',
    doc_type: 'visa',
    doc_label: 'Residence visa',
    document_number: null,
    expiry_date: '2026-11-20',
    days_left: 5,
    ...extra,
  };
}

describe('renewal links', () => {
  it('opens the owner page with the add form', () => {
    expect(renewalHref(item({}))).toBe('/employees/e1?add=visa');
    const tenancy = item({
      owner_kind: 'legal_entity',
      owner_id: 'le1',
      doc_type: 'tenancy_contract',
    });
    expect(renewalHref(tenancy)).toBe('/legal-entities/le1?add=tenancy_contract');
    expect(ownerHref(tenancy)).toBe('/legal-entities/le1');
    expect(renewalHref(item({ owner_kind: 'legal_entity', owner_id: 'le1', doc_type: 'trade_license' }))).toBe(
      '/legal-entities/le1?add=trade_license',
    );
  });
});

describe('groupByOwner', () => {
  it('puts companies first, then people by their most urgent paper, each soonest first', () => {
    const groups = groupByOwner([
      item({ owner_id: 'e1', owner_name: 'Juan', doc_type: 'ohc', days_left: 9 }),
      item({ owner_id: 'e2', owner_name: 'Ana', doc_type: 'visa', days_left: -2 }),
      item({ owner_id: 'e1', owner_name: 'Juan', doc_type: 'visa', days_left: 3 }),
      item({ owner_kind: 'legal_entity', owner_id: 'le1', owner_name: 'Fatema', doc_type: 'mohre_card', days_left: 12 }),
    ]);
    expect(groups.map((g) => g.name)).toEqual(['Fatema', 'Ana', 'Juan']);
    expect(groups[2].items.map((i) => i.doc_type)).toEqual(['visa', 'ohc']);
  });
});
