/**
 * Read a legal entity's registration document — an establishment card, a
 * labour company card, a chamber certificate, a tenancy or a permit — well
 * enough to prefill the form: the number printed beside its label, the issue
 * and expiry dates, and who issued it. Like the employee readers, it fills
 * only what the text clearly states and the super-admin checks every field.
 */
import type { EntityDocType } from '@/lib/pos-types';
import { dateRightAfter, first, issueAndExpiry, normalise } from './parse';

export type EntityDocumentFields = {
  document_number?: string;
  issue_date?: string;
  expiry_date?: string;
  issuing_authority?: string;
  warnings: string[];
};

const NUMBER = '([A-Z0-9][A-Z0-9/-]{2,24})';

/** The labels a type prints its number beside, most specific first. */
const NUMBER_LABELS: Record<EntityDocType, RegExp[]> = {
  establishment_card: [/Establishment\s*(?:No|Number|Card\s*No)\.?/i, /Card\s*(?:No|Number)\.?/i],
  mohre_card: [/Establishment\s*(?:No|Number|Code)\.?/i, /Company\s*(?:No|Code)\.?/i],
  chamber_certificate: [/Membership\s*(?:No|Number)\.?/i, /Member\s*(?:No|Number)\.?/i],
  corporate_tax: [/(?:Tax\s*Registration\s*Number|\bTRN\b)\.?/i],
  moa: [/(?:Notari[sz]ation|Reference|Contract)\s*(?:No|Number)\.?/i],
  tenancy_contract: [/Ejari\s*(?:No|Number|Contract\s*No)\.?/i, /Contract\s*(?:No|Number)\.?/i],
  food_permit: [/(?:Permit|Approval|Certificate|Licen[cs]e)\s*(?:No|Number)\.?/i],
  civil_defence: [/Certificate\s*(?:No|Number)\.?/i, /(?:Permit|Reference)\s*(?:No|Number)\.?/i],
  signage_permit: [/Permit\s*(?:No|Number)\.?/i, /(?:Reference|Application)\s*(?:No|Number)\.?/i],
  pest_control: [/Contract\s*(?:No|Number)\.?/i, /(?:Reference|Agreement)\s*(?:No|Number)\.?/i],
};

/** Issuers recognisable from the letterhead, spelled as the console suggests. */
const AUTHORITIES: Array<[RegExp, string]> = [
  [/Identity\s*,?\s*Citizenship|\bICP\b/i, 'ICP (Sharjah)'],
  [/General\s*Directorate\s*of\s*Residency|\bGDRFA\b/i, 'GDRFA Dubai'],
  [/Human\s*Resources\s*(?:&|and)\s*Emirati[sz]ation|\bMOHRE\b/i, 'Ministry of Human Resources & Emiratisation (MOHRE)'],
  [/Sharjah\s*Chamber/i, 'Sharjah Chamber of Commerce & Industry'],
  [/Dubai\s*Chambers?/i, 'Dubai Chambers'],
  [/Federal\s*Tax\s*Authority/i, 'Federal Tax Authority (FTA)'],
  [/Ejari|Dubai\s*Land\s*Department|\bRERA\b/i, 'Dubai Land Department (Ejari)'],
  [/Sharjah\s*Civil\s*Defen[cs]e/i, 'Sharjah Civil Defence Authority'],
  [/Dubai\s*Civil\s*Defen[cs]e/i, 'Dubai Civil Defence'],
  [/Dubai\s*Municipality/i, 'Dubai Municipality — Food Safety Department'],
  [/Sharjah\s*(?:City\s*)?Municipality/i, 'Sharjah City Municipality'],
];

export function parseEntityDocument(type: EntityDocType, raw: string, expires = true): EntityDocumentFields {
  const text = normalise(raw);
  const out: EntityDocumentFields = { warnings: [] };
  for (const label of NUMBER_LABELS[type]) {
    const found = first(text, new RegExp(`${label.source}\\s*:?\\s*${NUMBER}`, 'i'));
    if (found && /\d/.test(found)) {
      out.document_number = found;
      break;
    }
  }
  const issueLabel =
    /Issue\s*Date|Date\s*of\s*Issue|Issued\s*(?:on|date)?|Start\s*Date|Valid\s*From|Effective\s*(?:Registration\s*)?Date/i;
  const expiryLabel = /Expiry\s*Date|Date\s*of\s*Expiry|Expiration\s*Date|Valid\s*(?:Until|To|Till)|End\s*Date/i;
  // A card prints "Issue Date: … Expiry Date: …" on one line: read the date
  // straight after each label first, the looser nearest-date rule after.
  const loose = issueAndExpiry(text, issueLabel, expiryLabel, { window: 120 });
  const expiry = dateRightAfter(text, expiryLabel) ?? loose.expiry;
  let issue = dateRightAfter(text, issueLabel) ?? loose.issue;
  if (issue && expiry && issue >= expiry) issue = undefined;
  const guessed = !dateRightAfter(text, expiryLabel) && loose.guessed;
  if (expires) {
    out.issue_date = issue;
    out.expiry_date = expiry;
    if (guessed) out.warnings.push('Expiry date guessed as the latest date on the document.');
  } else {
    // Issued once: the one date worth reading is the issue / effective date.
    out.issue_date = issue ?? expiry;
  }
  out.issuing_authority = AUTHORITIES.find(([re]) => re.test(text))?.[1];
  return out;
}
