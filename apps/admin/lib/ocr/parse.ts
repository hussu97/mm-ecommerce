/**
 * Turn the text of a scanned UAE work document into form fields.
 *
 * Pure functions over a string, so they are unit-tested against real OCR /
 * PDF-text output (`parse.test.ts`). The text comes from `extract.ts`: a PDF's
 * own text layer when it has one (the municipality OHC, MOHRE and ICP
 * e-documents do), otherwise Tesseract run in the browser.
 *
 * Everything returned is a *suggestion* the super-admin reviews before saving —
 * OCR misreads, and a wrong expiry date is worse than an empty one, so a field
 * the text does not clearly support is left out rather than guessed.
 */

export type OcrDocType = 'passport' | 'visa' | 'labour_card' | 'ohc' | 'trade_license';

/** What a scan can tell us about the person it belongs to. */
export type PersonFields = {
  full_name?: string;
  full_name_ar?: string;
  nationality?: string;
  date_of_birth?: string;
  gender?: 'M' | 'F';
  job_title?: string;
  personal_number?: string;
  /** The sponsoring establishment as printed, matched to a legal entity by the UI. */
  establishment?: string;
};

export type DocumentFields = {
  document_number?: string;
  issue_date?: string;
  expiry_date?: string;
  issuing_authority?: string;
  details: Record<string, string>;
  person: PersonFields;
  /** Human notes on how sure the read is (failed MRZ check digit, etc.). */
  warnings: string[];
};

// ─── Dates ───────────────────────────────────────────────────────────────────

const MONTHS: Array<[RegExp, number]> = [
  // OCR reads J as 1/l/I and O as 0, so the month words are matched loosely.
  [/^[j1il]an/i, 1],
  [/^feb/i, 2],
  [/^mar/i, 3],
  [/^apr/i, 4],
  [/^ma[y¥]/i, 5],
  [/^[j1il]un/i, 6],
  [/^[j1il]u[l1i]/i, 7],
  [/^aug/i, 8],
  [/^sep/i, 9],
  [/^[o0]ct/i, 10],
  [/^n[o0]v/i, 11],
  [/^dec/i, 12],
];

function monthOf(word: string): number | null {
  for (const [re, m] of MONTHS) if (re.test(word)) return m;
  return null;
}

function iso(y: number, m: number, d: number): string | null {
  if (y < 1900 || y > 2100 || m < 1 || m > 12 || d < 1 || d > 31) return null;
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCMonth() !== m - 1) return null; // 31 Feb
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

const MON = '[A-Za-z0-9¥]{3,9}';
/** Every way a date is written on these documents, all with a 4-digit year
 *  (a 2-digit year is a print timestamp or the MRZ, handled separately). */
const DATE_PATTERNS: Array<[RegExp, (m: RegExpMatchArray) => string | null]> = [
  // 2027/09/02, 2027-09-02
  [/\b(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})\b/g, (m) => iso(+m[1], +m[2], +m[3])],
  // 02/09/2027, 02-09-2027, 02.09.2027 — day first, as everywhere in the UAE
  [/\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b/g, (m) => iso(+m[3], +m[2], +m[1])],
  // 16/Jul/2028, 16-JUL-2028, 16 JUL 2028, 16 July 2028
  [
    new RegExp(`\\b(\\d{1,2})[\\s/.-]{0,3}(${MON})[\\s/.,-]{0,3}(\\d{4})\\b`, 'g'),
    (m) => {
      const month = monthOf(m[2]);
      return month ? iso(+m[3], month, +m[1]) : null;
    },
  ],
  // Jul 16, 2028
  [
    new RegExp(`\\b(${MON})\\s+(\\d{1,2}),?\\s+(\\d{4})\\b`, 'g'),
    (m) => {
      const month = monthOf(m[1]);
      return month ? iso(+m[3], month, +m[2]) : null;
    },
  ],
];

type DateHit = { start: number; end: number; value: string };

function dateHits(text: string): DateHit[] {
  const hits: DateHit[] = [];
  for (const [re, toIso] of DATE_PATTERNS) {
    for (const m of text.matchAll(re)) {
      const value = toIso(m);
      const start = m.index ?? 0;
      if (value) hits.push({ start, end: start + m[0].length, value });
    }
  }
  return hits.sort((a, b) => a.start - b.start);
}

/** Every date in *text*, in reading order, de-duplicated. */
export function findDates(text: string): string[] {
  return [...new Set(dateHits(text).map((h) => h.value))];
}

function globalRe(label: RegExp): RegExp {
  return new RegExp(label.source, label.flags.includes('g') ? label.flags : `${label.flags}g`);
}

/**
 * The date that belongs to a label. Bilingual UAE documents put the value on
 * either side of its English label (the Arabic label sits across from it), so
 * the nearest date on the label's own line wins; failing that, the first date
 * within *window* characters after it — OCR's sparse pass often moves a value
 * a line or two away from its label.
 */
function dateNear(text: string, label: RegExp, window = 80): string | undefined {
  const hits = dateHits(text);
  for (const m of text.matchAll(globalRe(label))) {
    const from = m.index ?? 0;
    const to = from + m[0].length;
    const lineStart = text.lastIndexOf('\n', from - 1) + 1;
    const nl = text.indexOf('\n', to);
    const lineEnd = nl === -1 ? text.length : nl;
    const sameLine = hits
      .filter((h) => h.start >= lineStart && h.end <= lineEnd)
      .map((h) => ({ h, gap: h.start >= to ? h.start - to : from - h.end }))
      .sort((a, b) => a.gap - b.gap);
    if (sameLine.length) return sameLine[0].h.value;
  }
  for (const m of text.matchAll(globalRe(label))) {
    const start = (m.index ?? 0) + m[0].length;
    const found = findDates(text.slice(start, start + window));
    if (found.length) return found[0];
  }
  return undefined;
}

/** Labelled issue/expiry, made consistent: a missing or out-of-order pair
 *  falls back to the latest date (expiry) and the earliest before it (issue). */
function issueAndExpiry(
  text: string,
  issueLabel: RegExp,
  expiryLabel: RegExp,
  { exclude = [], window = 80 }: { exclude?: Array<string | undefined>; window?: number } = {},
): { issue?: string; expiry?: string; guessed: boolean } {
  const all = findDates(text)
    .filter((d) => !exclude.includes(d))
    .sort();
  let issue = dateNear(text, issueLabel, window);
  let expiry = dateNear(text, expiryLabel, window);
  let guessed = false;
  if (!expiry) {
    expiry = all[all.length - 1];
    guessed = Boolean(expiry);
  }
  if (issue && expiry && issue >= expiry) issue = undefined;
  if (!issue && expiry) {
    const before = all.filter((d) => d < expiry!);
    if (before.length && all.length >= 2) issue = before[0];
  }
  return { issue, expiry, guessed };
}

// ─── Small helpers ───────────────────────────────────────────────────────────

/** Fold Arabic presentation forms (what PDF text layers carry) to plain
 *  letters, and tidy whitespace. */
export function normalise(text: string): string {
  return text
    .normalize('NFKC')
    .replace(/[ـ]/g, '') // tatweel
    .replace(/[ \t]+/g, ' ')
    .replace(/\r/g, '');
}

function clean(value: string | undefined): string | undefined {
  const v = value?.replace(/\s+/g, ' ').trim().replace(/[.,:;]+$/, '');
  return v ? v : undefined;
}

function first(text: string, re: RegExp, group = 1): string | undefined {
  return clean(text.match(re)?.[group]);
}

const ARABIC_LINE = /^[؀-ۿ\s]{4,}$/;

function arabicLines(text: string): string[] {
  return text
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => ARABIC_LINE.test(l));
}

function titleCase(value: string): string {
  return value.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

/** ICAO 3-letter codes of the nationalities a UAE bakery is likely to employ. */
const COUNTRY_CODES: Record<string, string> = {
  ARE: 'United Arab Emirates',
  BGD: 'Bangladesh',
  CHN: 'China',
  EGY: 'Egypt',
  ETH: 'Ethiopia',
  GBR: 'United Kingdom',
  GHA: 'Ghana',
  IDN: 'Indonesia',
  IND: 'India',
  IRN: 'Iran',
  JOR: 'Jordan',
  KEN: 'Kenya',
  LBN: 'Lebanon',
  LKA: 'Sri Lanka',
  MAR: 'Morocco',
  MMR: 'Myanmar',
  NGA: 'Nigeria',
  NPL: 'Nepal',
  PAK: 'Pakistan',
  PHL: 'Philippines',
  SDN: 'Sudan',
  SYR: 'Syria',
  THA: 'Thailand',
  TUN: 'Tunisia',
  UGA: 'Uganda',
  USA: 'United States',
  VNM: 'Vietnam',
  YEM: 'Yemen',
};

/** Arabic nationality words (as the MOHRE/municipality cards print them). */
const ARABIC_NATIONALITIES: Array<[RegExp, string]> = [
  [/فلبين/, 'Philippines'],
  [/هند/, 'India'],
  [/باكستان/, 'Pakistan'],
  [/بنغلاد|بنجلاد/, 'Bangladesh'],
  [/نيبال/, 'Nepal'],
  [/سريلان|سيريلان/, 'Sri Lanka'],
  [/مصر/, 'Egypt'],
  [/اثيوبي|إثيوبي/, 'Ethiopia'],
  [/كيني/, 'Kenya'],
  [/اوغند|أوغند/, 'Uganda'],
  [/اندونيس|إندونيس/, 'Indonesia'],
  [/سوري/, 'Syria'],
  [/اردن|أردن/, 'Jordan'],
];

function arabicNationality(text: string): string | undefined {
  for (const [re, name] of ARABIC_NATIONALITIES) if (re.test(text)) return name;
  return undefined;
}

/** File-number prefixes: the first digit of `201/2024/…` is the emirate. */
const VISA_EMIRATES: Record<string, string> = {
  '1': 'Abu Dhabi',
  '2': 'Dubai',
  '3': 'Sharjah',
  '4': 'Ajman',
  '5': 'Umm Al Quwain',
  '6': 'Ras Al Khaimah',
  '7': 'Fujairah',
};

// ─── Passport MRZ (ICAO 9303 TD3) ────────────────────────────────────────────

const MRZ_WEIGHTS = [7, 3, 1];

function mrzValue(c: string): number {
  if (c >= '0' && c <= '9') return c.charCodeAt(0) - 48;
  if (c >= 'A' && c <= 'Z') return c.charCodeAt(0) - 55;
  return 0; // '<'
}

export function mrzCheckDigit(field: string): number {
  let sum = 0;
  for (let i = 0; i < field.length; i++) sum += mrzValue(field[i]) * MRZ_WEIGHTS[i % 3];
  return sum % 10;
}

/** OCR's usual confusions in a field that can only hold digits. */
function digitsOnly(s: string): string {
  return s
    .replace(/[OQDU]/g, '0')
    .replace(/[IL|]/g, '1')
    .replace(/Z/g, '2')
    .replace(/S/g, '5')
    .replace(/G/g, '6')
    .replace(/B/g, '8');
}

function mrzLine(raw: string): string {
  return raw
    .toUpperCase()
    .replace(/\s+/g, '')
    .replace(/[«‹(\[{]/g, '<');
}

/** Every TD3 line pair in the text — one per OCR pass that found it. */
function findMrzAll(text: string): Array<[string, string]> {
  const lines = text.split('\n').map(mrzLine).filter((l) => l.length >= 30);
  const pairs: Array<[string, string]> = [];
  for (let i = 0; i < lines.length - 1; i++) {
    const a = lines[i];
    const start = a.indexOf('P');
    if (start < 0) continue;
    const l1 = a.slice(start);
    if (!/^P[A-Z0-9<][A-Z<]{3}[A-Z<]+<<?[A-Z<]+/.test(l1)) continue;
    const l2 = lines[i + 1];
    if (!/\d{6}/.test(l2)) continue;
    pairs.push([l1.padEnd(44, '<').slice(0, 44), l2.padEnd(44, '<').slice(0, 44)]);
  }
  return pairs;
}

/** Letters and digits OCR mistakes for each other. */
const LOOKALIKES: Record<string, string[]> = {
  '0': ['O', 'D', 'Q'], O: ['0'], D: ['0'], Q: ['0'],
  '1': ['I', 'L'], I: ['1'], L: ['1'],
  '2': ['Z'], Z: ['2'],
  '5': ['S'], S: ['5'],
  '6': ['G'], G: ['6'],
  '8': ['B'], B: ['8'],
  '4': ['A'], A: ['4'],
  '7': ['T'], T: ['7'],
};

/** A passport number whose check digit fails: if swapping exactly one
 *  look-alike character makes it pass, and only one such swap does, that is
 *  almost certainly the misread. */
function correctByCheckDigit(field: string, check: string): string | null {
  const want = digitsOnly(check);
  const fixes = new Set<string>();
  for (let i = 0; i < field.length; i++) {
    for (const alt of LOOKALIKES[field[i]] ?? []) {
      const candidate = field.slice(0, i) + alt + field.slice(i + 1);
      if (String(mrzCheckDigit(candidate)) === want) fixes.add(candidate);
    }
  }
  if (fixes.size === 1) return [...fixes][0];
  // Several swaps satisfy the digit (one in ten does by chance). National
  // numbers are a letter prefix then digits (Z1234567, P1234567A, AB1234567),
  // and OCR's letter/digit confusion lands on the prefix — keep the swap that
  // yields that shape, if exactly one does.
  const shaped = [...fixes].filter((f) => /^[A-Z]{1,2}\d{6,8}[A-Z]?<*$/.test(f));
  return shaped.length === 1 ? shaped[0] : null;
}

function mrzDate(yymmdd: string, kind: 'birth' | 'expiry'): string | undefined {
  const d = digitsOnly(yymmdd);
  if (!/^\d{6}$/.test(d)) return undefined;
  const yy = +d.slice(0, 2);
  const nowYY = new Date().getUTCFullYear() % 100;
  const century = kind === 'expiry' ? 2000 : yy > nowYY ? 1900 : 2000;
  return iso(century + yy, +d.slice(2, 4), +d.slice(4, 6)) ?? undefined;
}

/** The MRZ read best across the OCR passes: the first pair whose check
 *  digits all pass, else the one with the fewest failures. */
export function parseMrz(text: string): DocumentFields | null {
  const parsed = findMrzAll(text).map(parseMrzPair);
  if (!parsed.length) return null;
  return parsed.reduce((best, p) => (p.warnings.length < best.warnings.length ? p : best));
}

function parseMrzPair([l1, l2]: [string, string]): DocumentFields {
  const warnings: string[] = [];

  // Names: "SURNAME<<GIVEN<NAMES"; a stray K/C among filler '<' is OCR noise.
  const nameField = l1.slice(5).replace(/(?<=<<)[KC](?=<<)/g, '<');
  const [surnameRaw, givenRaw = ''] = nameField.split('<<');
  const surname = clean(surnameRaw.replace(/</g, ' '));
  const given = clean(givenRaw.replace(/<+$/, '').replace(/</g, ' '));

  let number = l2.slice(0, 9);
  if (String(mrzCheckDigit(number)) !== digitsOnly(l2[9])) {
    const fixed = correctByCheckDigit(number, l2[9]);
    if (fixed) {
      number = fixed;
      warnings.push('One character of the passport number was corrected using its MRZ check digit — compare it with the scan.');
    }
  }
  const nationality = l2.slice(10, 13).replace(/[^A-Z]/g, '');
  const dob = l2.slice(13, 19);
  const sex = l2[20];
  const expiry = l2.slice(21, 27);

  const checks: Array<[string, string, string]> = [
    ['passport number', number, number === l2.slice(0, 9) ? l2[9] : String(mrzCheckDigit(number))],
    ['date of birth', digitsOnly(dob), l2[19]],
    ['expiry date', digitsOnly(expiry), l2[27]],
  ];
  for (const [label, field, digit] of checks) {
    if (String(mrzCheckDigit(field)) !== digitsOnly(digit)) {
      warnings.push(`The ${label} failed its MRZ check — compare it with the scan.`);
    }
  }

  const fields: DocumentFields = {
    document_number: clean(number.replace(/</g, '')),
    expiry_date: mrzDate(expiry, 'expiry'),
    issuing_authority: COUNTRY_CODES[l1.slice(2, 5).replace(/</g, '')] ?? clean(l1.slice(2, 5).replace(/</g, '')),
    details: {},
    person: {},
    warnings,
  };
  const country = COUNTRY_CODES[nationality] ?? nationality;
  const birth = mrzDate(dob, 'birth');
  if (surname) fields.details.surname = surname;
  if (given) fields.details.given_names = given;
  if (country) fields.details.nationality = country;
  if (birth) fields.details.date_of_birth = birth;
  if (sex === 'M' || sex === 'F') fields.details.sex = sex;

  fields.person = {
    full_name: [given, surname].filter(Boolean).join(' ') || undefined,
    nationality: country || undefined,
    date_of_birth: birth,
    gender: sex === 'M' || sex === 'F' ? sex : undefined,
  };
  return fields;
}

// ─── Per-document parsers ────────────────────────────────────────────────────

function empty(): DocumentFields {
  return { details: {}, person: {}, warnings: [] };
}

export function parsePassport(raw: string): DocumentFields {
  const text = normalise(raw);
  const out = parseMrz(text) ?? {
    ...empty(),
    warnings: ['No machine-readable zone found — fill the fields from the scan.'],
  };
  if (!out.document_number) {
    out.document_number = first(text, /Passport\s*(?:No|Number)\.?\s*:?\s*([A-Z]{0,2}\d{6,9}[A-Z]?)/i);
  }
  const dates = findDates(text);
  out.expiry_date ??= dateNear(text, /Date\s*of\s*Expiry|Expiry\s*Date|Valid\s*Until/i);
  const labelled = dateNear(text, /Date\s*of\s*Issue|Issue\s*Date|Issued\s*on/i);
  out.issue_date =
    labelled && out.expiry_date && labelled < out.expiry_date && labelled !== out.person.date_of_birth
      ? labelled
      : guessIssue(dates, out);
  const place = first(
    text,
    /(?:Place\s*of\s*Issue|Issuing\s*Authority|Authority)\s*[:/]?\s*([A-Z][A-Za-z ,.-]{2,40})(?:\n|$)/,
  );
  if (place) out.details.place_of_issue = place;
  const pob = first(text, /Place\s*of\s*Birth\s*[:/]?\s*([A-Z][A-Za-z ,.-]{2,40})(?:\n|$)/);
  if (pob) out.details.place_of_birth = pob;
  return out;
}

/** Without a labelled issue date: the date 5 or 10 years (less a day) before
 *  expiry if the page shows one, as passports are issued for those terms. */
function guessIssue(dates: string[], fields: DocumentFields): string | undefined {
  const expiry = fields.expiry_date;
  if (!expiry) return undefined;
  const dob = fields.person.date_of_birth;
  const end = Date.parse(expiry);
  const candidates = dates.filter((d) => d !== expiry && d !== dob && Date.parse(d) < end);
  const near = candidates.find((d) => {
    const years = (end - Date.parse(d)) / (365.25 * 86_400_000);
    return Math.abs(years - 10) < 0.1 || Math.abs(years - 5) < 0.1;
  });
  return near;
}

export function parseLabourCard(raw: string): DocumentFields {
  const text = normalise(raw);
  const out = empty();
  out.document_number = first(text, /Work\s*Permit\s*N[O0o]\.?\s*:?[^\d\n]{0,40}(\d{6,12})/i);
  const personal = first(text, /Personal\s*N[O0o]\.?\s*:?[^\d\n]{0,40}(\d{10,16})/i);
  if (personal) {
    out.details.personal_number = personal;
    out.person.personal_number = personal;
  }
  const name = first(text, /\bName\s*:\s*([A-Z][A-Z .'-]{2,80}?)\s*(?:\n|$)/);
  if (name) out.person.full_name = titleCase(name);
  const establishment = first(text, /Establishment\s*:\s*([A-Z0-9][A-Z0-9 &.,'()-]{2,80}?)\s*(?:\n|$)/);
  if (establishment) {
    out.details.establishment = establishment;
    out.person.establishment = establishment;
  }
  // Profession and nationality are printed in Arabic on the card; Tesseract's
  // English model turns Arabic into Latin noise, so only take a profession
  // written in capitals (an English one).
  const profession = first(text, /Profession\s*:\s*([A-Z][A-Z /&-]{2,60}?)\s*(?:\n|$)/);
  if (profession) {
    out.details.profession = titleCase(profession);
    out.person.job_title = titleCase(profession);
  }
  const nationality = arabicNationality(text) ?? first(text, /Nationality\s*:\s*([A-Z][A-Z ]{2,40}?)\s*(?:\n|$)/);
  if (nationality) out.person.nationality = titleCase(nationality);

  const dates = findDates(text);
  out.expiry_date = dateNear(text, /Expiry\s*Date/i, 200) ?? dates[dates.length - 1];
  out.issuing_authority = 'Ministry of Human Resources & Emiratisation';
  if (!out.expiry_date) out.warnings.push('No expiry date found — enter it from the card.');
  return out;
}

export function parseOhc(raw: string): DocumentFields {
  const text = normalise(raw);
  const out = empty();
  const card = text.match(/\b(\d{10})\s*([A-Z]{2})\b/);
  if (card) out.document_number = `${card[1]} ${card[2]}`;
  const audit = first(text, /Audit\s*Trail\s*:?\s*(\d{4,})/i) ?? first(text, /(\d{4,})\s*Audit\s*Trail/i);
  const ref = first(text, /\((\d{5,})\)/);
  const reference = [ref, audit && `audit ${audit}`].filter(Boolean).join(' · ');
  if (reference) out.details.reference = reference;
  const remarks = text
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => /vaccinat|hepatitis|\bfit\b|unfit|medically/i.test(l));
  if (remarks.length) out.details.result = remarks.join('; ');

  const { issue, expiry } = issueAndExpiry(
    text,
    /Issue\s*Date|Date\s*of\s*Issue/i,
    /Expiry\s*Date|Valid\s*(?:Until|Till)/i,
  );
  out.issue_date = issue;
  out.expiry_date = expiry;

  if (/\bSCM\b|Sharjah/i.test(text)) out.issuing_authority = 'Sharjah City Municipality';
  else if (/Dubai\s*Municipality|\bDM\b/.test(text)) out.issuing_authority = 'Dubai Municipality';
  else if (/Ajman/i.test(text)) out.issuing_authority = 'Ajman Municipality';

  const arabic = arabicLines(text);
  if (arabic.length) out.person.full_name_ar = arabic[0];
  const nationality = arabicNationality(text);
  if (nationality) out.person.nationality = nationality;
  return out;
}

export function parseVisa(raw: string): DocumentFields {
  const text = normalise(raw);
  const out = empty();
  const file =
    text.match(/\b([1-7]\d{2})\s*\/\s*(\d{4})\s*\/\s*(\d{1,9})\b/) ??
    text.match(/File\s*(?:No|Number)\.?\s*:?\s*([\d/ ]{8,})/i);
  if (file) {
    out.document_number = file.length >= 4 ? `${file[1]}/${file[2]}/${file[3]}` : clean(file[1]);
    const emirate = VISA_EMIRATES[(out.document_number ?? '')[0]];
    if (emirate) {
      out.details.place_of_issue = emirate;
      out.issuing_authority = emirate;
    }
  }
  const uid = first(text, /U\.?\s*I\.?\s*D\.?\s*(?:No|Number)?\.?\s*:?\s*(\d{6,12})/i);
  if (uid) out.details.uid_number = uid;
  const passport = first(text, /Passport\s*(?:No|Number)\.?\s*:?\s*([A-Z]{0,2}\d{6,9}[A-Z]?)/i);
  if (passport) out.details.passport_number = passport;
  const profession = first(text, /Profession\s*:?\s*([A-Za-z][A-Za-z /&-]{2,60}?)\s*(?:\n|$)/);
  if (profession) {
    out.details.profession = titleCase(profession);
    out.person.job_title = titleCase(profession);
  }
  const sponsor = first(
    text,
    /(?:Sponsor|Employer)(?:\s*Name)?\s*:?\s*([A-Z][A-Za-z0-9 &.,'()-]{2,80}?)\s*(?:\n|$)/,
  );
  if (sponsor) {
    out.details.sponsor = sponsor;
    out.person.establishment = sponsor;
  }
  const place = first(text, /Place\s*of\s*Issue\s*:?\s*([A-Za-z][A-Za-z ]{2,30}?)\s*(?:\n|$)/);
  if (place) {
    out.details.place_of_issue = titleCase(place);
    out.issuing_authority = titleCase(place);
  }
  const name = first(text, /(?:Full\s*)?Name\s*:?\s*([A-Z][A-Z .'-]{3,80}?)\s*(?:\n|$)/);
  if (name) out.person.full_name = titleCase(name);
  const nationality = first(text, /Nationality\s*:?\s*([A-Za-z][A-Za-z ]{2,40}?)\s*(?:\n|$)/);
  if (nationality) out.person.nationality = titleCase(nationality);
  const dob = dateNear(text, /Date\s*of\s*Birth|D\.?O\.?B/i, 40);
  if (dob) out.person.date_of_birth = dob;

  const { issue, expiry } = issueAndExpiry(
    text,
    /Issue\s*Date|Date\s*of\s*Issue|Issuing\s*Date/i,
    /Expiry\s*Date|Date\s*of\s*Expiry|Expiration\s*Date|Valid\s*Until/i,
    { exclude: [dob], window: 40 },
  );
  out.issue_date = issue;
  out.expiry_date = expiry;
  return out;
}

export type TradeLicenseFields = {
  license_number?: string;
  registration_number?: string;
  issue_date?: string;
  expiry_date?: string;
  /** A key of the API's TRADE_LICENSE_AUTHORITIES, when recognised. */
  issuing_authority?: string;
  emirate?: string;
  legal_name?: string;
  warnings: string[];
};

const LICENSE_AUTHORITIES: Array<[RegExp, string, string]> = [
  [/Sharjah\s*Economic|SEDD|Economic\s*Development\s*Department.*Sharjah/i, 'sharjah_sedd', 'Sharjah'],
  [/Department\s*of\s*Economy\s*(?:and|&)\s*Tourism|\bDET\b|Dubai\s*Economy|\bDED\b.*Dubai/i, 'dubai_det', 'Dubai'],
  [/Abu\s*Dhabi.*(?:Economic|ADDED)|\bADDED\b/i, 'abu_dhabi_ded', 'Abu Dhabi'],
  [/Ajman.*(?:Economic|DED)/i, 'ajman_ded', 'Ajman'],
  [/\bDMCC\b/i, 'dmcc', 'Dubai'],
  [/\bIFZA\b/i, 'ifza', 'Dubai'],
  [/\bRAKEZ\b/i, 'rakez', 'Ras Al Khaimah'],
  [/\bSPC\b|Sharjah\s*Publishing/i, 'spc', 'Sharjah'],
  [/\bSHAMS\b/i, 'shams', 'Sharjah'],
];

export function parseTradeLicense(raw: string): TradeLicenseFields {
  const text = normalise(raw);
  const out: TradeLicenseFields = { warnings: [] };
  out.license_number = first(text, /Licen[cs]e\s*(?:No|Number|#)\.?\s*:?\s*([A-Z0-9-]{3,15})/i);
  // The value sits on either side of the label ("193868 Registration No." on
  // SEDD's layout). Two numbers after the label (DET prints the chamber number
  // beside it) cannot be told apart, so that case is left for the human.
  const reg = /(?:Register|Registration|Commercial\s*Reg(?:istration|ister)?)\s*(?:No|Number)\.?/i;
  const after = text.match(new RegExp(`${reg.source}\\s*:?\\s*(\\d{3,15})(\\s+\\d{3,15})?`, 'i'));
  const before = text.match(new RegExp(`(\\d{3,15})\\s+${reg.source}`, 'i'));
  out.registration_number = after && !after[2] ? after[1] : before?.[1];
  const { issue, expiry, guessed } = issueAndExpiry(
    text,
    /Issue\s*Date|Date\s*of\s*Issue|Issuing\s*Date/i,
    /Expiry\s*Date|Date\s*of\s*Expiry|Expiration\s*Date|Valid\s*Until/i,
    // DET prints the company type on a line between the labels and the dates.
    { window: 140 },
  );
  out.issue_date = issue;
  out.expiry_date = expiry;
  if (guessed) out.warnings.push('Expiry date guessed as the latest date on the licence.');
  for (const [re, key, emirate] of LICENSE_AUTHORITIES) {
    if (re.test(text)) {
      out.issuing_authority = key;
      out.emirate = emirate;
      break;
    }
  }
  out.legal_name = first(text, /(?:Trade\s*Name|Company\s*Name|Legal\s*Name)\s*:?\s*([A-Z][A-Za-z0-9 &.,'()-]{2,100}?)\s*(?:\n|$)/);
  return out;
}

export function parseDocument(type: Exclude<OcrDocType, 'trade_license'>, text: string): DocumentFields {
  switch (type) {
    case 'passport':
      return parsePassport(text);
    case 'visa':
      return parseVisa(text);
    case 'labour_card':
      return parseLabourCard(text);
    case 'ohc':
      return parseOhc(text);
  }
}
