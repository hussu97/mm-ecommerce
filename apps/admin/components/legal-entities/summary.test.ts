import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import type { EntityDocumentTypeSpec, LegalEntity } from '@/lib/pos-types';
import { summariseEntity } from './summary';

beforeAll(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-15T08:00:00Z'));
});
afterAll(() => vi.useRealTimers());

const spec = (doc_type: string, label: string, expires = true) =>
  ({ doc_type, label, expires }) as unknown as EntityDocumentTypeSpec;
const specs = [
  spec('establishment_card', 'Establishment card'),
  spec('moa', 'Memorandum of Association', false),
  spec('tenancy_contract', 'Tenancy contract'),
];
const entity = (extra: Partial<LegalEntity> = {}) =>
  ({
    id: 'le1',
    vat_registered: false,
    has_vat_certificate: false,
    trade_license_expiry: '2027-07-18',
    ...extra,
  }) as LegalEntity;

describe('summariseEntity', () => {
  it('leads with the most urgent and counts a no-expiry document as valid', () => {
    const s = summariseEntity(
      entity(),
      {
        legal_entity_id: 'le1',
        current_documents: [
          { id: 'a', doc_type: 'establishment_card', document_number: null, expiry_date: '2026-11-24' },
          { id: 'b', doc_type: 'moa', document_number: null, expiry_date: null },
          { id: 'c', doc_type: 'tenancy_contract', document_number: null, expiry_date: '2027-01-01' },
        ],
      },
      specs,
    );
    expect(s.headline).toBe('Establishment card expires in 9 days');
    expect(s.items.map((i) => [i.spec.doc_type, i.tone])).toEqual([
      ['trade_license', 'ok'],
      ['establishment_card', 'due'],
      ['moa', 'ok'],
      ['tenancy_contract', 'soon'],
    ]);
  });

  it('expects a VAT certificate only from a registered entity', () => {
    const s = summariseEntity(entity({ vat_registered: true }), undefined, specs);
    expect(s.items.map((i) => i.spec.doc_type)).toContain('vat_certificate');
    expect(s.headline).toBe('4 documents not on file');
  });
});
