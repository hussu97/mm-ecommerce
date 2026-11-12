import { describe, expect, it } from 'vitest';
import { suggestFromText, titleFromFilename } from './classify';

describe('other-file suggestions', () => {
  it('takes the type from the title, not a later mention', () => {
    const text = `MELTING MOMENTS SWEETS LLC
EMPLOYMENT CONTRACT
This contract is made between… The employee is entitled to 30 days annual leave.`;
    expect(suggestFromText(text, 'scan0001.pdf')).toEqual({
      title: 'Employment Contract',
      category: 'Employment contract',
    });
  });

  it('uses the type as the name when the phrase sits in a paragraph', () => {
    const text =
      'To whom it may concern, this is to certify that the bearer has completed the Person in Charge food safety course run by Dubai Municipality on 3 March 2026.';
    expect(suggestFromText(text)).toEqual({
      title: 'Food safety certificate',
      category: 'Food safety certificate',
    });
  });

  it('keeps a mixed-case heading as written', () => {
    expect(suggestFromText('No Objection Certificate\nWe have no objection…').title).toBe(
      'No Objection Certificate',
    );
  });

  it('falls back to the file name when the text names no kind of paper', () => {
    expect(suggestFromText('lorem ipsum', 'Ahmed_bank-details.pdf')).toEqual({ title: 'Ahmed bank details' });
    expect(suggestFromText('lorem ipsum')).toEqual({ title: undefined });
  });

  it('does not read NOC out of a longer word', () => {
    expect(suggestFromText('KNOCK KNOCK').category).toBeUndefined();
  });

  it('tidies a file name', () => {
    expect(titleFromFilename('IMG_2024 scan-01.jpeg')).toBe('IMG 2024 scan 01');
    expect(titleFromFilename('.pdf')).toBeUndefined();
  });
});
