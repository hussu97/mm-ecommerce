import { describe, expect, it } from 'vitest';
import { parseEntityDocument } from './entity';

describe('parseEntityDocument', () => {
  it('reads an establishment card', () => {
    const text = [
      'Federal Authority for Identity, Citizenship, Customs & Port Security',
      'Establishment Card',
      'Establishment No: 3/1/123456',
      'Issue Date: 03/11/2025   Expiry Date: 02/11/2026',
    ].join('\n');
    expect(parseEntityDocument('establishment_card', text)).toEqual({
      document_number: '3/1/123456',
      issue_date: '2025-11-03',
      expiry_date: '2026-11-02',
      issuing_authority: 'ICP (Sharjah)',
      warnings: [],
    });
  });

  it('reads a chamber membership number and guesses a missing expiry label', () => {
    const out = parseEntityDocument(
      'chamber_certificate',
      'Sharjah Chamber of Commerce & Industry\nMembership No. 778179\n03/11/2025 02/11/2026',
    );
    expect(out.document_number).toBe('778179');
    expect(out.expiry_date).toBe('2026-11-02');
    expect(out.issuing_authority).toBe('Sharjah Chamber of Commerce & Industry');
    expect(out.warnings).toHaveLength(1);
  });

  it('keeps only the issue date for a document issued once', () => {
    const out = parseEntityDocument(
      'corporate_tax',
      'Federal Tax Authority\nTax Registration Number: 100123456700003\nEffective Registration Date: 01/06/2024',
      false,
    );
    expect(out.document_number).toBe('100123456700003');
    expect(out.issue_date).toBe('2024-06-01');
    expect(out.expiry_date).toBeUndefined();
  });
});
