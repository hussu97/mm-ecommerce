/**
 * Suggest a name and a type for an employee's "other" file from its text.
 *
 * These papers have no fixed layout, so nothing is parsed out of them: the
 * text is only searched for the phrases that say what kind of paper it is
 * ("Employment Contract", "No Objection Certificate", "Food Safety"). The
 * phrase mentioned *first* wins — a document's title comes before its body,
 * and a contract that later mentions annual leave is still a contract. The
 * name is that title line when it is short enough to be one, else the type,
 * else the uploaded file's own name. Everything is a suggestion the
 * super-admin can overwrite.
 */
import { normalise } from './parse';

/** Kinds of paper a UAE bakery keeps on its staff, in the console's spelling. */
const KINDS: Array<[RegExp, string]> = [
  [/employment\s+contract|contract\s+of\s+employment|labou?r\s+contract|service\s+agreement/i, 'Employment contract'],
  [/offer\s+letter|letter\s+of\s+offer|offer\s+of\s+employment|job\s+offer/i, 'Offer letter'],
  [/salary\s+certificate/i, 'Salary certificate'],
  [/pay\s*slip|salary\s+slip|pay\s+statement/i, 'Payslip'],
  [/experience\s+(?:certificate|letter)|certificate\s+of\s+experience|relieving\s+letter/i, 'Experience certificate'],
  [/no\s+objection\s+(?:certificate|letter)/i, 'NOC'],
  // The abbreviation only in capitals, so "knock" or "noc" in prose is not one.
  [/\bNOC\b/, 'NOC'],
  [/bank\s+statement|statement\s+of\s+account|account\s+statement/i, 'Bank statement'],
  [/police\s+clearance|good\s+conduct|criminal\s+record/i, 'Police clearance'],
  [/food\s+safety|food\s+hygiene|person\s+in\s+charge|\bPIC\b\s+certificate|\bHACCP\b/i, 'Food safety certificate'],
  [/training\s+certificate|certificate\s+of\s+(?:training|attendance|participation)/i, 'Training certificate'],
  [/diploma|bachelor|master\s+of|degree\s+certificate|transcript|university|school\s+leaving|secondary\s+school/i, 'Educational certificate'],
  [/driving\s+licen[cs]e|driver'?s\s+licen[cs]e/i, 'Driving licence'],
  [/sick\s+leave|medical\s+leave/i, 'Sick leave certificate'],
  [/medical\s+(?:report|certificate|fitness)|fitness\s+certificate|lab(?:oratory)?\s+report/i, 'Medical report'],
  [/warning\s+letter|written\s+warning|final\s+warning|show\s+cause/i, 'Warning letter'],
  [/resignation/i, 'Resignation letter'],
  [/termination\s+(?:letter|notice)|notice\s+of\s+termination|letter\s+of\s+termination/i, 'Termination letter'],
  [/final\s+settlement|end\s+of\s+service|gratuity/i, 'Final settlement'],
  [/leave\s+application|application\s+for\s+leave|leave\s+request|vacation\s+request/i, 'Leave application'],
  [/entry\s+permit/i, 'Entry permit'],
  [/ejari|tenancy\s+contract|lease\s+agreement/i, 'Tenancy contract'],
  [/birth\s+certificate/i, 'Birth certificate'],
  [/marriage\s+certificate/i, 'Marriage certificate'],
  [/undertaking/i, 'Undertaking'],
  [/declaration/i, 'Declaration'],
];

export type FileSuggestion = { title?: string; category?: string };

/** "IMG_2024 scan-01.pdf" → "IMG 2024 scan 01" — a fallback name, nothing more. */
export function titleFromFilename(name: string): string | undefined {
  const base = name.replace(/\.[A-Za-z0-9]{1,5}$/, '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
  return base || undefined;
}

function tidyTitle(line: string): string {
  const clean = line.replace(/\s+/g, ' ').replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9).]+$/g, '').trim();
  // A shouted heading reads better in title case; mixed case is kept as typed.
  return clean === clean.toUpperCase() ? clean.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : clean;
}

export function suggestFromText(raw: string, filename?: string): FileSuggestion {
  const text = normalise(raw);
  let best: { at: number; kind: string; match: string } | undefined;
  for (const [re, kind] of KINDS) {
    const m = re.exec(text);
    if (m && (best === undefined || m.index < best.at)) best = { at: m.index, kind, match: m[0] };
  }
  const fallback = filename ? titleFromFilename(filename) : undefined;
  if (!best) return { title: fallback };

  // The line the phrase sits on is the title when it is short enough to be one.
  const start = text.lastIndexOf('\n', best.at) + 1;
  const nl = text.indexOf('\n', best.at);
  const line = text.slice(start, nl === -1 ? text.length : nl).trim();
  const words = line.split(/\s+/).length;
  const title = line.length <= 80 && words <= 10 ? tidyTitle(line) : best.kind;
  return { title: title || best.kind, category: best.kind };
}
