/**
 * Turn the text of a scanned UAE work document into form fields.
 *
 * Pure functions over a string, so they are unit-tested against real OCR /
 * PDF-text output (`parse.test.ts`). The text comes from `extract.ts`: a PDF's
 * own text layer when it has one (the municipality OHC, MOHRE and ICP
 * e-documents do), otherwise Tesseract run in the browser. The passport and the
 * Emirates ID also carry a machine-readable zone whose check digits say which
 * OCR reading of a number or date to trust.
 *
 * Everything returned is a *suggestion* the super-admin reviews before saving —
 * OCR misreads, and a wrong expiry date is worse than an empty one, so a field
 * the text does not clearly support is left out rather than guessed.
 */

export type OcrDocType = 'passport' | 'visa' | 'emirates_id' | 'labour_card' | 'ohc' | 'trade_license';

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
  const pairs = findMrzAll(text);
  const parsed = pairs.map(parseMrzPair);
  if (!parsed.length) return null;
  const best = parsed.reduce((b, p) => (p.warnings.length < b.warnings.length ? p : b));
  // The numbers come from the pass whose check digits pass; the names (which
  // have no check digit) from what the passes agree on.
  const surname = consensus(parsed.map((p) => p.details.surname));
  const given = consensus(parsed.map((p) => p.details.given_names));
  setNames(best, surname, given);
  if (pairs.some(([l1]) => mrzNames(l1.slice(5)).noisy)) {
    best.warnings.push('The names in the machine-readable zone were hard to read — check them against the scan.');
  }
  return best;
}

function setNames(fields: DocumentFields, surname?: string, given?: string) {
  if (surname) fields.details.surname = surname;
  else delete fields.details.surname;
  if (given) fields.details.given_names = given;
  else delete fields.details.given_names;
  fields.person.full_name = [given, surname].filter(Boolean).join(' ') || undefined;
}

/** Characters Tesseract returns for the '<' filler. */
const FILLER_LOOKALIKES = 'KLC';
const LOOKALIKE_ONLY = /^[KLC]+$/;

/**
 * Split a TD3 name field ("SURNAME<<GIVEN<NAMES<<<…") into surname and given
 * names, undoing OCR's habit of reading the '<' filler as K, L or C — a
 * Philippine passport came back as `TAPSIN<K<KADARC<LLLLLLL…` for
 * `TAPSIN<<ADAR<<<<<<<…`. `noisy` says a repair was needed.
 */
export function mrzNames(field: string): { surname?: string; given?: string; noisy: boolean } {
  let noisy = false;
  let f = field;
  // The trailing filler. When OCR has turned it into look-alike letters, a
  // single look-alike glued to the last name ("ADARC<LLL") goes with it; a
  // clean filler ("MICHAEL<<<<") leaves the name alone.
  const tail = f.match(/[<KLC]*$/)?.[0] ?? '';
  const filler = tail.includes('<') ? tail.slice(tail.indexOf('<')) : tail;
  if (tail.length >= 3 && /[KLC]/.test(filler)) {
    noisy = true;
    f = f.slice(0, f.length - tail.length);
  } else {
    f = f.replace(/<+$/, '');
  }
  // Surname runs up to the first separator: an empty token ('<<') or a token
  // that is only look-alikes ('<K<').
  const tokens = f.split('<');
  const surname: string[] = [];
  const given: string[] = [];
  // A field that opens with the separator has no surname (Indian passports
  // can print the whole name as given names).
  let inGiven = f.startsWith('<');
  for (const token of tokens) {
    if (!token || LOOKALIKE_ONLY.test(token)) {
      if (token) noisy = true;
      if (surname.length) inGiven = true;
      continue;
    }
    (inGiven ? given : surname).push(token);
  }
  return {
    surname: surname.join(' ') || undefined,
    given: given.join(' ') || undefined,
    noisy,
  };
}

/** Across OCR passes, the name most of them read — but a reading that is
 *  another one plus stray look-alike letters at its edges ("KADAR" vs
 *  "ADAR") loses to the shorter one, whatever the count. */
function consensus(values: Array<string | undefined>): string | undefined {
  const seen = values.filter((v): v is string => Boolean(v));
  if (!seen.length) return undefined;
  const edge = new RegExp(`^[${FILLER_LOOKALIKES}]+|[${FILLER_LOOKALIKES}]+$`, 'g');
  const stripsTo = (long: string, short: string) =>
    long !== short && long.split(' ').map((w) => w.replace(edge, '')).join(' ') === short;
  const kept = seen.filter((v) => !seen.some((other) => stripsTo(v, other)));
  const counts = new Map<string, number>();
  for (const v of kept) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].length - b[0].length)[0][0];
}

function parseMrzPair([l1, l2]: [string, string]): DocumentFields {
  const warnings: string[] = [];

  const names = mrzNames(l1.slice(5));
  const surname = names.surname;
  const given = names.given;

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

export type PassportHint = {
  /** The employee's name as already on file — renewals are the common case,
   *  and it settles letters OCR could not. */
  knownName?: string | null;
};

/** Words of the name on file, for snapping an OCR'd name token onto. */
function nameWords(name: string | null | undefined): string[] {
  return (name ?? '')
    .toUpperCase()
    .split(/[^A-Z]+/)
    .filter((w) => w.length >= 3);
}

/** "KADAR" → "ADAR" when the employee is called Adar: a token that is a name
 *  word plus look-alike letters at its edges is that word. */
function snapToKnown(value: string | undefined, words: string[]): string | undefined {
  if (!value || !words.length) return value;
  return value
    .split(' ')
    .map((token) => {
      if (words.includes(token)) return token;
      const hit = words.find(
        (w) => token.includes(w) && token.replace(w, '').split('').every((c) => FILLER_LOOKALIKES.includes(c)),
      );
      return hit ?? token;
    })
    .join(' ');
}

const VIZ_NAME = /^[A-Z][A-Z' -]{1,40}$/;

/** The value printed under a visual-zone label ("Surname" / "TAPSIN"), only
 *  when it reads as a name — the hologram behind these fields often turns
 *  them into noise. */
function vizValue(text: string, label: RegExp, within = 1): string | undefined {
  const lines = text.split('\n').map((l) => l.trim());
  for (let i = 0; i < lines.length; i++) {
    if (!label.test(lines[i])) continue;
    // The first `within` non-empty lines under the label; a date or noise
    // between label and value (OCR's sparse pass) is skipped over.
    const next = lines
      .slice(i + 1)
      .filter((l) => l.length > 0)
      .slice(0, within)
      .find((l) => VIZ_NAME.test(l));
    if (next) return next.replace(/\s+/g, ' ');
  }
  return undefined;
}

export function parsePassport(raw: string, hint: PassportHint = {}): DocumentFields {
  const text = normalise(raw);
  const out = parseMrz(text) ?? {
    ...empty(),
    warnings: ['No machine-readable zone found — fill the fields from the scan.'],
  };

  // Names: the MRZ consensus, checked against the printed fields and the name
  // on file. A printed surname that the MRZ also contains settles it.
  const words = nameWords(hint.knownName);
  let surname = snapToKnown(out.details.surname, words);
  let given = snapToKnown(out.details.given_names, words);
  const vizSurname = vizValue(text, /\bSurname\b/i);
  if (vizSurname && (!surname || surname.replace(/ /g, '').includes(vizSurname.replace(/ /g, '')))) {
    surname = vizSurname;
  }
  const vizGiven = vizValue(text, /\bGiven\s*names?\b/i);
  if (vizGiven && (!given || given.includes(vizGiven))) given = vizGiven;
  setNames(out, surname, given);

  // Some passports (the Philippines') print a middle name the MRZ leaves out.
  if (/\bMiddle\s*name\b/i.test(text)) {
    const printed = vizValue(text, /\bMiddle\s*name\b/i);
    const taken = new Set([...(surname ?? '').split(' '), ...(given ?? '').split(' ')]);
    const fromFile = words.filter((w) => !taken.has(w)).join(' ');
    const middle = printed && (!words.length || words.includes(printed)) ? printed : fromFile || undefined;
    if (middle) {
      out.details.middle_name = middle;
      out.person.full_name = [given, middle, surname].filter(Boolean).join(' ');
    }
  }

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
  // The printed issuing authority ("DFA PAGADIAN") beats the MRZ's country
  // code. OCR mangles these small labels ("//ssuing cuthority"), hence the
  // loose patterns; Philippine passports also give it away by shape.
  const authority =
    sameLineValue(text, /Place\s*of\s*Issue|Issuing\s*Authority/i, /[A-Z][A-Z ,.-]{2,40}/) ??
    vizValue(text, /Place\s*of\s*Issue|[il]?ssuing\s*\S?uthority/i, 3) ??
    first(text, /\b(DFA[ \t]+[A-Z]{3,}(?:[ \t][A-Z]{3,})?)\b/);
  if (authority) {
    out.details.place_of_issue = authority;
    out.issuing_authority = authority;
  }
  const pob =
    sameLineValue(text, /Place\s*of\s*Birth/i, /[A-Z][A-Z ,.-]{2,40}/) ??
    vizValue(text, /Place\W+\w{0,3}\W*birth/i);
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
    // 301/2025/7654321, or the four-part form 301/2025/3/0035771.
    text.match(/\b([1-7]\d{2})\s*\/\s*(\d{4})\s*\/\s*(\d{1,9})(?:\s*\/\s*(\d{1,9}))?\b/) ??
    text.match(/File\s*(?:No|Number)\.?\s*:?\s*([\d/ ]{8,})/i);
  if (file) {
    out.document_number =
      file.length >= 4 ? [file[1], file[2], file[3], file[4]].filter(Boolean).join('/') : clean(file[1]);
    const emirate = VISA_EMIRATES[(out.document_number ?? '')[0]];
    if (emirate) {
      out.details.place_of_issue = emirate;
      out.issuing_authority = emirate;
    }
  }
  // Older residence stickers print each value LEFT of its English label (the
  // Arabic label is further right), and OCR garbles the small labels — so a
  // value is taken from the label's own line, after or before it, and the
  // fields with a fixed shape are found by shape when the label is unreadable.
  const uid =
    first(text, /U\.?\s*I\.?\s*D\.?\s*(?:No|Number)?\.?[ \t]*:?[ \t]*(\d{6,15})/i) ??
    // The U.I.D. box carries the 15-digit identity number (784-YYYY-…).
    first(text, /\b(784\d{12})\b/);
  if (uid) out.details.uid_number = uid;
  const passport =
    first(text, /Passport\s*(?:No|Number)\.?[ \t]*:?[ \t]*([A-Z]{0,2}\d{6,9}[A-Z]?)/i) ??
    first(text, /\b([A-Z]{1,2}\d{7,8}[A-Z]?)\b/);
  if (passport) out.details.passport_number = passport;
  const profession = sameLineValue(text, /Profession/i, /[A-Z][A-Z /&-]{2,60}/);
  if (profession) {
    out.details.profession = titleCase(profession);
    out.person.job_title = titleCase(profession);
  }
  const sponsor = sameLineValue(text, /(?:Sponsor|Employer)(?:\s*Name)?/i, /[A-Z][A-Z0-9 &.,'()-]{2,80}/);
  if (sponsor) {
    out.details.sponsor = sponsor;
    out.person.establishment = sponsor;
  }
  const place = sameLineValue(text, /Place\s*of\s*Issue/i, /[A-Z][A-Za-z ]{2,30}/);
  if (place) {
    out.details.place_of_issue = titleCase(place);
    out.issuing_authority = titleCase(place);
  }
  const name = sameLineValue(text, /(?:Full\s*)?Name/i, /[A-Z][A-Z .'-]{3,80}/);
  if (name) out.person.full_name = titleCase(name);
  const nationality = sameLineValue(text, /Nationality/i, /[A-Z][A-Za-z ]{2,40}/);
  if (nationality) out.person.nationality = titleCase(nationality);
  const dob = dateNear(text, /Date\s*of\s*Birth|D\.?O\.?B/i, 40);
  if (dob) out.person.date_of_birth = dob;

  // A residence visa carries only its issue and expiry dates (and, on the
  // newer e-visa, the birth date), and the labels sit wherever the layout
  // puts them — the old sticker prints "Expiry Date" between the two dates.
  // So the order decides: the earlier is the issue, the later the expiry.
  const dates = findDates(text)
    .filter((d) => d !== dob)
    .sort();
  out.expiry_date = dates[dates.length - 1];
  out.issue_date = dates.length >= 2 ? dates[0] : undefined;
  return out;
}

// ─── Emirates ID (front labels + ICAO 9303 TD1 MRZ on the back) ──────────────

/** ICP — the issuer of every Emirates ID, whichever emirate prints it. */
const EID_AUTHORITY = 'Federal Authority for Identity, Citizenship, Customs & Port Security';

/** 784-YYYY-NNNNNNN-C, however OCR spaced or dashed it. */
const EID_NUMBER = /\b784[\s.-]{0,2}(\d{4})[\s.-]{0,2}(\d{7})[\s.-]{0,2}(\d)\b/;

function formatEid(digits: string): string {
  return `${digits.slice(0, 3)}-${digits.slice(3, 7)}-${digits.slice(7, 14)}-${digits.slice(14)}`;
}

export type Td1 = {
  /** The 9-digit card number (changes with every card). */
  cardNumber: string;
  cardOk: boolean;
  /** The 15-digit identity number from the optional-data field, when present. */
  idNumber?: string;
  /** The composite check digit — which covers the identity number — passed. */
  compositeOk: boolean;
  birth?: string;
  birthOk: boolean;
  expiry?: string;
  expiryOk: boolean;
  sex?: 'M' | 'F';
  nationality?: string;
  surname?: string;
  given?: string;
  noisy: boolean;
  failures: number;
};

/** Every TD1 line triple in the text — one per OCR pass that found it. The
 *  UAE card opens `ILARE` (or `IDARE`); the names line may lose its trailing
 *  filler to OCR, so it is not held to a length. */
function findTd1All(text: string): Array<[string, string, string]> {
  const lines = text
    .split('\n')
    .map(mrzLine)
    .filter((l) => l.length > 0);
  const triples: Array<[string, string, string]> = [];
  for (let i = 0; i < lines.length - 2; i++) {
    const start = lines[i].search(/[I1L][A-Z<]ARE\d/);
    if (start < 0 || lines[i].length - start < 25) continue;
    const l2 = lines[i + 1];
    if (!/^\d{7}/.test(digitsOnly(l2.slice(0, 7)))) continue;
    const l3 = lines[i + 2];
    if (!/^[A-Z<]{5,}$/.test(l3) || !l3.includes('<')) continue;
    const pad = (l: string) => l.padEnd(30, '<').slice(0, 30);
    triples.push([pad(lines[i].slice(start)), pad(l2), pad(l3)]);
  }
  return triples;
}

function parseTd1Triple([l1, l2, l3]: [string, string, string]): Td1 {
  const ok = (field: string, digit: string) => String(mrzCheckDigit(field)) === digitsOnly(digit);
  const cardNumber = digitsOnly(l1.slice(5, 14));
  const optional = digitsOnly(l1.slice(15, 30).replace(/[KLC]+$/, '')).replace(/<+$/, '');
  const dob = digitsOnly(l2.slice(0, 6));
  const expiry = digitsOnly(l2.slice(8, 14));
  // Line 2's own optional field is filler on the UAE card; OCR reads that
  // filler as K/L/C, which would sink the composite check for nothing.
  const composite =
    digitsOnly(l1.slice(5, 30)) + digitsOnly(l2.slice(0, 7)) + digitsOnly(l2.slice(8, 15)) + l2.slice(18, 29).replace(/[KLC]/g, '<');
  const sex = l2[7];
  const nationality = l2.slice(15, 18).replace(/[^A-Z]/g, '');
  const names = mrzNames(l3);
  const td1: Td1 = {
    cardNumber,
    cardOk: ok(cardNumber, l1[14]),
    idNumber: /^784\d{12}$/.test(optional) ? optional : undefined,
    compositeOk: ok(composite, l2[29]),
    birth: mrzDate(dob, 'birth'),
    birthOk: ok(dob, l2[6]),
    expiry: mrzDate(expiry, 'expiry'),
    expiryOk: ok(expiry, l2[14]),
    sex: sex === 'M' || sex === 'F' ? sex : undefined,
    nationality: nationality ? COUNTRY_CODES[nationality] ?? nationality : undefined,
    surname: names.surname,
    given: names.given,
    noisy: names.noisy,
    failures: 0,
  };
  td1.failures = [td1.cardOk, td1.compositeOk, td1.birthOk, td1.expiryOk].filter((v) => !v).length;
  return td1;
}

/** The TD1 read best across the OCR passes (fewest failed check digits), with
 *  the names — which have no check digit — taken from what the passes agree on. */
export function parseTd1(text: string): Td1 | null {
  const parsed = findTd1All(text).map(parseTd1Triple);
  if (!parsed.length) return null;
  const best = parsed.reduce((b, p) => (p.failures < b.failures ? p : b));
  best.surname = consensus(parsed.map((p) => p.surname));
  best.given = consensus(parsed.map((p) => p.given));
  best.noisy = parsed.some((p) => p.noisy);
  return best;
}

/** The value after a label on its own line ("Occupation: Baker"). The card's
 *  Arabic labels are stripped first, so a value ending the English half of a
 *  bilingual line still reaches the line end. */
function afterLabel(latin: string, label: RegExp, value: RegExp): string | undefined {
  // Case-sensitive on purpose: `i` would let PRINTED_NAME's capitals match
  // lowercase OCR noise. The labels spell out the cases the cards print.
  return first(latin, new RegExp(`${label.source}[ \\t]*[:.]?[ \\t]*(${value.source})[ \\t]*(?:\\n|$)`, 'm'));
}

/**
 * The date printed straight after a label ("Expiry Date: 10/03/2028"), with
 * nothing but a colon between. The card prints issue and expiry side by side
 * on one line, where `dateNear`'s nearest-on-either-side rule hands the
 * expiry label the issue date that ends one space before it; this reading
 * comes first, and `dateNear` stays the fallback for looser layouts.
 */
function dateRightAfter(text: string, label: RegExp): string | undefined {
  for (const m of text.matchAll(globalRe(label))) {
    const rest = text.slice((m.index ?? 0) + m[0].length);
    const lead = rest.match(/^[ \t]*[:.]?[ \t]*/)?.[0].length ?? 0;
    const hit = dateHits(rest.slice(lead, lead + 24))[0];
    if (hit && hit.start === 0) return hit.value;
  }
  return undefined;
}

const EID_ISSUE = /Issu(?:ing|e)\s*Date|Date\s*of\s*Issue/i;
const EID_EXPIRY = /Expiry\s*Date|Date\s*of\s*Expiry|Expiration\s*Date/i;
const EID_BIRTH = /Date\s*of\s*Birth|D\.?O\.?B/i;

/** Two to six capitalised words — a printed name, not OCR's Latin noise. */
const PRINTED_NAME = /[A-Z][A-Za-z'-]+(?:[ ][A-Z][A-Za-z'-]+){1,5}/;

export function parseEmiratesId(raw: string): DocumentFields {
  const text = normalise(raw);
  const latin = text.replace(/[؀-ۿ]+/g, ' ').replace(/[ \t]+/g, ' ');
  const out = empty();
  out.issuing_authority = EID_AUTHORITY;
  const mrz = parseTd1(text);

  // Identity number: the MRZ's when its composite check covers it, else the
  // one printed on the front, else the MRZ's unverified.
  const printed = text.match(EID_NUMBER);
  const printedId = printed ? `784-${printed[1]}-${printed[2]}-${printed[3]}` : undefined;
  const mrzId = mrz?.idNumber ? formatEid(mrz.idNumber) : undefined;
  out.document_number = (mrz?.compositeOk ? mrzId : undefined) ?? printedId ?? mrzId;
  if (printedId && mrzId && printedId !== mrzId) {
    out.warnings.push('The ID number on the front and in the machine-readable zone differ — check it against the card.');
  }

  const printedCard = first(latin, /Card\s*(?:No|Number)\.?[ \t]*:?[^\d\n]{0,30}(\d{9})\b/i);
  const card = (mrz?.cardOk ? mrz.cardNumber : undefined) ?? printedCard ?? (mrz ? mrz.cardNumber : undefined);
  if (card && /^\d{9}$/.test(card)) out.details.card_number = card;

  // Dates: a check-digit-verified MRZ date beats a printed one, and a printed
  // one beats an MRZ date whose check failed.
  const printedDob = dateRightAfter(latin, EID_BIRTH) ?? dateNear(latin, EID_BIRTH, 40);
  const dob = (mrz?.birthOk ? mrz.birth : undefined) ?? printedDob ?? mrz?.birth;
  const labelled = issueAndExpiry(latin, EID_ISSUE, EID_EXPIRY, { exclude: [dob, printedDob, mrz?.birth] });
  const printedExpiry = dateRightAfter(latin, EID_EXPIRY) ?? (labelled.guessed ? undefined : labelled.expiry);
  const printedIssue = dateRightAfter(latin, EID_ISSUE) ?? labelled.issue;
  out.expiry_date = (mrz?.expiryOk ? mrz.expiry : undefined) ?? printedExpiry ?? mrz?.expiry ?? labelled.expiry;
  if (!out.expiry_date) out.warnings.push('No expiry date found — enter it from the card.');
  else if (out.expiry_date === labelled.expiry && labelled.guessed && !mrz?.expiry) {
    out.warnings.push('Expiry date guessed as the latest date on the card — check it.');
  }
  if (printedIssue && out.expiry_date && printedIssue < out.expiry_date) out.issue_date = printedIssue;

  // Warn only where a value that failed its check digit is the one filled in.
  // A failed MRZ field with a printed reading to fall back on is the check
  // doing its job (Tesseract reading 9001158 as 0001158), not news.
  if (mrz) {
    const failed = [
      !mrz.cardOk && !printedCard && card === mrz.cardNumber && 'card number',
      // The composite covers every field, so it only casts doubt on the ID
      // number when no other field's own check failed to explain it.
      !mrz.compositeOk && mrz.cardOk && mrz.birthOk && mrz.expiryOk && !printedId && out.document_number === mrzId && 'ID number',
      !mrz.birthOk && !printedDob && dob === mrz.birth && 'date of birth',
      !mrz.expiryOk && !printedExpiry && out.expiry_date === mrz.expiry && 'expiry date',
    ].filter(Boolean);
    if (failed.length) {
      out.warnings.push(`The ${failed.join(', ')} failed the machine-readable zone check — compare with the card.`);
    }
  }
  if (!mrz) {
    out.warnings.push('No machine-readable zone found — include the back of the card to check the numbers.');
  }

  const sex = (first(latin, /\b(?:Sex|Gender)[ \t]*:?[ \t]*([MF])\b/) as 'M' | 'F' | undefined) ?? mrz?.sex;
  const nationality =
    afterLabel(latin, /\b(?:Nationality|NATIONALITY)/, /[A-Z][A-Za-z ]{2,40}/) ?? mrz?.nationality ?? arabicNationality(text);
  const printedName = afterLabel(latin, /\b(?:Name|NAME)/, PRINTED_NAME);
  const mrzName = [mrz?.given, mrz?.surname].filter(Boolean).join(' ');
  const name = printedName ? titleCase(printedName) : mrzName ? titleCase(mrzName) : undefined;
  if (!printedName && mrzName && mrz?.noisy) {
    out.warnings.push('The name in the machine-readable zone was hard to read — check it against the card.');
  }
  const occupation = afterLabel(latin, /\b(?:Occupation|OCCUPATION|Profession|PROFESSION)/, /[A-Z][A-Za-z /&-]{2,60}/);
  const employer = afterLabel(latin, /\b(?:Employer|EMPLOYER)(?:\s*Name)?/, /[A-Z][A-Za-z0-9 &.,'()-]{2,80}/);
  const place = afterLabel(latin, /\b(?:Issuing\s*Place|ISSUING\s*PLACE|Place\s*of\s*Issue)/, /[A-Z][A-Za-z ]{2,30}/);

  if (name) out.details.name = name;
  if (nationality) out.details.nationality = titleCase(nationality);
  if (dob) out.details.date_of_birth = dob;
  if (sex) out.details.sex = sex;
  if (occupation) out.details.occupation = titleCase(occupation);
  if (employer) out.details.employer = employer;
  if (place) out.details.place_of_issue = titleCase(place);

  out.person = {
    full_name: name,
    nationality: nationality ? titleCase(nationality) : undefined,
    date_of_birth: dob,
    gender: sex,
    job_title: occupation ? titleCase(occupation) : undefined,
    establishment: employer,
  };
  return out;
}

/**
 * The value printed beside a label on the label's own line — after it
 * ("Profession : NONE") or before it ("NONE     Profession", the old residence
 * sticker's layout). Never crosses a line: OCR's sparse pass separates labels
 * from values, and reading "the next line" there picks up noise.
 */
function sameLineValue(text: string, label: RegExp, value: RegExp): string | undefined {
  const after = new RegExp(`${label.source}[ \\t]*[:.]?[ \\t]*(${value.source})[ \\t]*(?:\\n|$)`, 'm');
  // `normalise` has already folded runs of spaces to one, so the value is the
  // capitalised run straight before the label.
  const before = new RegExp(`(?:^|[ \\t])(${value.source}?)[ \\t]+${label.source}`, 'm');
  return clean(text.match(after)?.[1] ?? text.match(before)?.[1]);
}

export type TradeLicenseFields = {
  license_number?: string;
  registration_number?: string;
  issue_date?: string;
  expiry_date?: string;
  /** A key of the API's TRADE_LICENSE_AUTHORITIES, when recognised. */
  issuing_authority?: string;
  emirate?: string;
  /** A key of the API's TRADE_LICENSE_TYPES (commercial, professional, …). */
  license_type?: string;
  /** The legal form, spelled as the shop records it. */
  legal_form?: string;
  legal_name?: string;
  warnings: string[];
};

/** The licence's title ("Trading License", "Commercial License") → its type.
 *  Sharjah calls a commercial licence a trading licence. */
const LICENSE_TYPES: Array<[RegExp, string]> = [
  [/\b(?:Trading|Commercial)\s+Licen[cs]e\b/i, 'commercial'],
  [/\bProfessional\s+Licen[cs]e\b/i, 'professional'],
  [/\bIndustrial\s+Licen[cs]e\b/i, 'industrial'],
  [/\bTouris[mt]\s+Licen[cs]e\b/i, 'tourism'],
  [/\bCrafts?\s+Licen[cs]e\b/i, 'crafts'],
  [/\bAgricultur(?:al|e)\s+Licen[cs]e\b/i, 'agricultural'],
  [/\be-?Trader\s+Licen[cs]e\b/i, 'e_trader'],
  [/\bFreelance(?:r)?\s+(?:Licen[cs]e|Permit)\b/i, 'freelance'],
];

/** UAE legal forms, most specific first, matched in the English text wherever
 *  the layout puts them (SEDD: beside "Legal status"; DET: a line above "Legal
 *  Type"), and returned in the shop's spelling. */
const LEGAL_FORMS: Array<[RegExp, string]> = [
  [/Limited\s+Liability\s+Company\s*-?\s*Single\s+Owner/i, 'Limited Liability Company - Single Owner (LLC - SO)'],
  [/Limited\s+Liability\s+Company/i, 'Limited Liability Company (LLC)'],
  [/Services?\s+Agency/i, 'Services Agency'],
  [/Sole\s+Establishment/i, 'Sole Establishment'],
  [/Sole\s+Proprietorship/i, 'Sole Proprietorship'],
  [/Civil\s+Company/i, 'Civil Company'],
  [/Branch\s+of\s+(?:a\s+)?GCC\s+Company/i, 'Branch of a GCC Company'],
  [/Branch\s+of\s+(?:a\s+)?Foreign\s+Company/i, 'Branch of a Foreign Company'],
  [/Free\s+Zone\s+Establishment|\bFZE\b/i, 'Free Zone Establishment (FZE)'],
  [/Free\s+Zone\s+(?:Limited\s+Liability\s+)?Company|\bFZ-?LLC\b|\bFZCO\b/i, 'Free Zone Company'],
  [/Private\s+Joint\s+Stock/i, 'Private Joint Stock Company'],
  [/Public\s+Joint\s+Stock/i, 'Public Joint Stock Company'],
];

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
  out.license_type = LICENSE_TYPES.find(([re]) => re.test(text))?.[1];
  out.legal_form = LEGAL_FORMS.find(([re]) => re.test(text))?.[1];
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

export function parseDocument(
  type: Exclude<OcrDocType, 'trade_license'>,
  text: string,
  hint: PassportHint = {},
): DocumentFields {
  switch (type) {
    case 'passport':
      return parsePassport(text, hint);
    case 'visa':
      return parseVisa(text);
    case 'emirates_id':
      return parseEmiratesId(text);
    case 'labour_card':
      return parseLabourCard(text);
    case 'ohc':
      return parseOhc(text);
  }
}
