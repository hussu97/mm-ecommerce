import type { EntityDocumentTypeSpec, EntityPaperworkOverview, LegalEntity } from '@/lib/pos-types';
import { summarisePaperwork, type PaperSummary } from '@/components/people/EmployeePaperwork';

/** The documents an entity is expected to hold, in strip order. */
export function expectedSpecs(entity: LegalEntity, specs: EntityDocumentTypeSpec[]) {
  return [
    { doc_type: 'trade_license', label: 'Trade licence' },
    ...(entity.vat_registered ? [{ doc_type: 'vat_certificate', label: 'VAT certificate' }] : []),
    ...specs,
  ];
}

export function summariseEntity(
  entity: LegalEntity,
  overview: EntityPaperworkOverview | undefined,
  specs: EntityDocumentTypeSpec[],
): PaperSummary {
  const current: Record<string, { expiry_date?: string | null } | undefined> = {};
  if (entity.trade_license_expiry) current.trade_license = { expiry_date: entity.trade_license_expiry };
  if (entity.has_vat_certificate) current.vat_certificate = { expiry_date: null };
  for (const d of overview?.current_documents ?? []) current[d.doc_type] = { expiry_date: d.expiry_date };
  return summarisePaperwork(current, expectedSpecs(entity, specs));
}
