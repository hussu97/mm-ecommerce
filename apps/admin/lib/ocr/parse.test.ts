import { readFileSync } from 'fs';
import { join } from 'path';
import { describe, expect, it } from 'vitest';
import { textFromPdfItems } from './extract';
import {
  findDates,
  mrzCheckDigit,
  mrzNames,
  parseLabourCard,
  parseMrz,
  parseOhc,
  parsePassport,
  parseTradeLicense,
  parseVisa,
} from './parse';

// Fixtures are real reader output with the person's name and numbers swapped
// for made-up ones: the Tesseract text of a MOHRE labour card (block + sparse
// passes) and the pdf.js text items of a Sharjah municipality OHC.
const FIXTURES = join(__dirname, '__fixtures__');
const labourCardText = readFileSync(join(FIXTURES, 'labour-card.ocr.txt'), 'utf8');
const ohcText = textFromPdfItems(
  JSON.parse(readFileSync(join(FIXTURES, 'ohc.pdf-items.json'), 'utf8')),
);

describe('findDates', () => {
  it('reads every UAE date style, including OCR-mangled month names', () => {
    expect(findDates('2027/09/02 and 02/09/2027 and 16/1ul/2028 and 3 OCT 2030')).toEqual([
      '2027-09-02',
      '2028-07-16',
      '2030-10-03',
    ]);
  });

  it('ignores two-digit-year timestamps and impossible dates', () => {
    expect(findDates('printed 02-09-26 13:43:45, 31/02/2027')).toEqual([]);
  });
});

describe('labour card (Tesseract)', () => {
  const out = parseLabourCard(labourCardText);

  it('reads the permit and personal numbers beside their labels', () => {
    expect(out.document_number).toBe('123456789');
    expect(out.details.personal_number).toBe('30400000000001');
    expect(out.person.personal_number).toBe('30400000000001');
  });

  it('finds the expiry the sparse pass pulled away from its label', () => {
    expect(out.expiry_date).toBe('2028-07-16');
  });

  it('reads the name and establishment, but not Arabic noise as a profession', () => {
    expect(out.person.full_name).toBe('Juan Santos Cruz');
    expect(out.details.establishment).toBe('FATEMA CAKE SWEETS');
    expect(out.details.profession).toBeUndefined();
  });
});

describe('OHC (PDF text layer)', () => {
  const out = parseOhc(ohcText);

  it('reads the card number, issue and expiry', () => {
    expect(out.document_number).toBe('2026001234 QU');
    expect(out.issue_date).toBe('2026-09-02');
    expect(out.expiry_date).toBe('2027-09-02');
  });

  it('reads the issuer, the remarks and the Arabic name + nationality', () => {
    expect(out.issuing_authority).toBe('Sharjah City Municipality');
    expect(out.details.result).toBe('Hepatitis B Vaccination NOT Complete');
    expect(out.details.reference).toBe('5300001 · audit 4700001');
    expect(out.person.full_name_ar).toBe('خوان سانتوس');
    expect(out.person.nationality).toBe('Philippines');
  });
});

// The ICAO 9303 specimen ("Anna Maria Eriksson", Utopia).
const SPECIMEN = [
  'P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<',
  'L898902C36UTO7408122F1204159ZE184226B<<<<<10',
].join('\n');

describe('passport MRZ', () => {
  it('computes ICAO check digits', () => {
    expect(mrzCheckDigit('L898902C3')).toBe(6);
    expect(mrzCheckDigit('740812')).toBe(2);
    expect(mrzCheckDigit('120415')).toBe(9);
  });

  it('parses the specimen with every check passing', () => {
    const out = parseMrz(SPECIMEN)!;
    expect(out.warnings).toEqual([]);
    expect(out.document_number).toBe('L898902C3');
    expect(out.expiry_date).toBe('2012-04-15');
    expect(out.details).toMatchObject({
      surname: 'ERIKSSON',
      given_names: 'ANNA MARIA',
      date_of_birth: '1974-08-12',
      sex: 'F',
      nationality: 'UTO',
    });
    expect(out.person.full_name).toBe('ANNA MARIA ERIKSSON');
  });

  it('survives OCR spacing and filler noise, and flags a misread digit', () => {
    const noisy = [
      'P<UTOERIKSSON<<ANNA<MARIA<<K<<<<<<<<<<«<<<<<',
      'L898902C3 6UTO7408122F1204169ZE184226B<<<<<10',
    ].join('\n');
    const out = parseMrz(noisy)!;
    expect(out.details.given_names).toBe('ANNA MARIA');
    expect(out.warnings).toEqual([
      'The expiry date failed its MRZ check — compare it with the scan.',
      'The names in the machine-readable zone were hard to read — check them against the scan.',
    ]);
  });

  it('repairs a letter prefix OCR read as a digit, using the check digit', () => {
    // An Indian-style number (Z + 7 digits) whose Z the OCR read as 2.
    const line1 = 'P<IND<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<<<<<<<<<';
    const good = 'Z4405178<0IND6903114F3102178<<<<<<<<<<<<<<<<';
    expect(parseMrz(`${line1}\n${good}`)!.warnings).toEqual([]);
    const out = parseMrz(`${line1}\n${good.replace(/^Z/, '2')}`)!;
    expect(out.document_number).toBe('Z4405178');
    expect(out.warnings).toEqual([
      'One character of the passport number was corrected using its MRZ check digit — compare it with the scan.',
    ]);
  });

  it('leaves an ambiguous misread alone and says so', () => {
    const out = parseMrz(SPECIMEN.replace('L898902C36', 'LB98902C36'))!;
    expect(out.document_number).toBe('LB98902C3');
    expect(out.warnings).toEqual(['The passport number failed its MRZ check — compare it with the scan.']);
  });

  it('prefers the OCR pass whose check digits pass', () => {
    const bad = SPECIMEN.replace('L898902C36', 'LB98902C36').replace('7408122', '7408129');
    const out = parseMrz(`${bad}\n\n${SPECIMEN}`)!;
    expect(out.warnings).toEqual([]);
    expect(out.document_number).toBe('L898902C3');
  });

  it('reads a name with no surname (as Indian passports print it)', () => {
    const out = parseMrz(
      ['P<IND<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<<<<<<<<<', 'L898902C36IND7408122F1204159ZE184226B<<<<<10'].join('\n'),
    )!;
    expect(out.details.surname).toBeUndefined();
    expect(out.person.full_name).toBe('ANNA MARIA');
  });

  it('takes a labelled issue date, or the one ten years before expiry', () => {
    const page = `REPUBLIC OF UTOPIA\nDate of Issue 16 APR 2002\n${SPECIMEN}`;
    expect(parsePassport(page).issue_date).toBe('2002-04-16');
    const unlabelled = `16 APR 2002\n12 AUG 1974\n${SPECIMEN}`;
    expect(parsePassport(unlabelled).issue_date).toBe('2002-04-16');
  });
});

describe('passport names through a noisy MRZ', () => {
  // The shape three Tesseract passes produced for a real Philippine passport
  // (`SANTOS<<JUAN<<<…` on the page; identity values made up): the '<' filler
  // read as K/L/C, so the '<<' separator never appears.
  const line2 = 'P1234567A1PHL9003152M3312315<<<<<<K<KLK<K<KLKKK08';
  const passes = [
    `P<PHLSANTOS<K<KJUANC<LLLLLLLLLLLLLLLLLLLLLLLLKLK\n${line2}`,
    `P<PHLSANTOS<K<JUANLLLLLLLLLLLLLLLLLLLLLLLLKKLKLKL\n${line2}`,
    `P<PHLSANTOS<K<KJUANC<LLLLLLLLLLLLLLLLLLLLLLLLKLK\n${line2}`,
  ].join('\n\n');

  it('splits a look-alike separator and drops the look-alike filler', () => {
    expect(mrzNames('SANTOS<K<JUANLLLLLLLLLLLLLLKKLKL')).toEqual({ surname: 'SANTOS', given: 'JUAN', noisy: true });
    expect(mrzNames('ERIKSSON<<ANNA<MARIA<<<<<<<<<<')).toEqual({ surname: 'ERIKSSON', given: 'ANNA MARIA', noisy: false });
    expect(mrzNames('MICHAEL<<<<<<<<<<<<<<<<<<<<<<<')).toEqual({ surname: 'MICHAEL', given: undefined, noisy: false });
  });

  it('takes the reading the passes agree on, minus stray edge letters', () => {
    const out = parsePassport(passes);
    expect(out.details.surname).toBe('SANTOS');
    expect(out.details.given_names).toBe('JUAN');
    expect(out.document_number).toBe('P1234567A');
    expect(out.warnings).toContain(
      'The names in the machine-readable zone were hard to read — check them against the scan.',
    );
  });

  it('reads the printed authority and place of birth under garbled labels', () => {
    const page = [
      'Lugar ng kapanganakan/Place Bi birth',
      'MANILA',
      'Maykapangyarihang nagkaloob//ssuing cuthority',
      '08 0CT 20',
      'DFA MANILA',
      passes,
    ].join('\n\n');
    const out = parsePassport(page);
    expect(out.issuing_authority).toBe('DFA MANILA');
    expect(out.details).toMatchObject({ place_of_issue: 'DFA MANILA', place_of_birth: 'MANILA' });
  });

  it('snaps to the name on file and fills the printed-only middle name', () => {
    const page = `Apelyido/Surname\n\nSANTOS\n\nPangalan/ Given names\n\n5\n\nPanggitnang apelyido/Middle name\n\nPAS!\n\n${passes}`;
    const out = parsePassport(page, { knownName: 'Juan Dela Santos' });
    expect(out.details).toMatchObject({ surname: 'SANTOS', given_names: 'JUAN', middle_name: 'DELA' });
    expect(out.person.full_name).toBe('JUAN DELA SANTOS');
  });
});

describe('residence visa', () => {
  it('reads the old sticker: values left of their labels, labels garbled, expiry label between the dates', () => {
    // The layout (pdf/OCR text) of a real Sharjah residence sticker; identity values made up.
    const out = parseVisa(
      [
        '                784000000000001 1 sss-ll ai',
        'SHAR dam aay               301/2025/3/0001234 + 44',
        ':         ay        P1234567A . fol oh',
        'NONE     Profession',
        'JUAN DELA CRUZ',
        '.     2027/08/13      ay      2025/08/14     fuohd Jigs',
        'Profession',
        'Sse a rns',
        '2027/08/13',
        'Expiry Date',
        '2025/08/14',
      ].join('\n'),
    );
    expect(out.document_number).toBe('301/2025/3/0001234');
    expect(out.issue_date).toBe('2025-08-14');
    expect(out.expiry_date).toBe('2027-08-13');
    expect(out.details).toMatchObject({
      uid_number: '784000000000001',
      passport_number: 'P1234567A',
      profession: 'None',
      place_of_issue: 'Sharjah',
    });
  });

  it('keeps every part of a four-part file number', () => {
    expect(parseVisa('File 301/2025/3/0035771\nExpiry Date 2027/08/13').document_number).toBe('301/2025/3/0035771');
  });

  const out = parseVisa(
    [
      'RESIDENCE',
      'File No 301/2025/7654321',
      'U.I.D No 123456789',
      'Passport No P1234567A',
      'Full Name JUAN SANTOS CRUZ',
      'Nationality Philippines',
      'Date of Birth 12/03/1990',
      'Profession SALES EXECUTIVE',
      'Sponsor FATEMA CAKE SWEETS',
      'Issue Date 05/01/2025',
      'Expiry Date 04/01/2027',
    ].join('\n'),
  );

  it('reads the file number and derives the emirate from it', () => {
    expect(out.document_number).toBe('301/2025/7654321');
    expect(out.issuing_authority).toBe('Sharjah');
  });

  it('reads the labelled fields and dates without mistaking the birth date', () => {
    expect(out.details).toMatchObject({
      uid_number: '123456789',
      passport_number: 'P1234567A',
      profession: 'Sales Executive',
      sponsor: 'FATEMA CAKE SWEETS',
    });
    expect(out.issue_date).toBe('2025-01-05');
    expect(out.expiry_date).toBe('2027-01-04');
    expect(out.person.date_of_birth).toBe('1990-03-12');
  });
});

describe('trade licence', () => {
  // The layouts of the shop's real licences (pdf.js text): SEDD prints a value
  // on either side of its English label; DET puts both dates on the next line,
  // expiry first, and the chamber number beside the register number.
  it('reads a Sharjah SEDD licence', () => {
    const out = parseTradeLicense(
      [
        'Trading License',
        'ﺗﻔﺎﺻﻴﻞ ﺍﻟﺮﺧﺼﺔ License Details',
        'ﺭﻗﻢ ﺍﻟﺮﺧﺼﺔ 02-11-2020 Issue Date   ﺗﺎﺭﻳﺦ ﺍﻹﺻﺪﺍﺭ   License No.   778179',
        'Expiry Date   02-11-2027   ﺗﺎﺭﻳﺦ ﺍﻹﻧﺘﻬﺎﺀ   193868 Registration No.   ﺭﻗﻢ ﺍﻟﺴﺠﻞ',
        'To view license details please visit SEDD web site: www.sedd.ae',
        'ﻭﻛﻴﻞ ﺧﺪﻣﺎﺕ Services Agency   ﺍﻟﺸﻜﻞ ﺍﻟﻘﺎﻧﻮﻧﻲ Legal status',
        'ﺗﺎﺭﻳﺦ ﺍﻟﻄﺒﺎﻋﺔ 30-10-2026   : Printing Date / 4 1',
      ].join('\n'),
    );
    expect(out).toMatchObject({
      license_number: '778179',
      registration_number: '193868',
      issue_date: '2020-11-02',
      expiry_date: '2027-11-02',
      issuing_authority: 'sharjah_sedd',
      emirate: 'Sharjah',
      license_type: 'commercial',
      legal_form: 'Services Agency',
      warnings: [],
    });
  });

  it('reads a Dubai DET licence and leaves an ambiguous register number blank', () => {
    const out = parseTradeLicense(
      [
        'Commercial License',
        'License No.   1081755',
        'Issue Date   تاريخ الصدار تاريخ النتهاء Expiry Date',
        'Limited Liability Company - Single Owner(LLC - SO)',
        '18/07/2027   19/07/2022',
        'DCCI No.   عضوية الغرفة Register No.   486122 1764353   رقم السجل التجارى',
        'Print Date   11:20 21/07/2026   تاريخ الطباعة',
        'issued without signature by the department of Economy and Tourism',
      ].join('\n'),
    );
    expect(out).toMatchObject({
      license_number: '1081755',
      issue_date: '2022-07-19',
      expiry_date: '2027-07-18',
      issuing_authority: 'dubai_det',
      emirate: 'Dubai',
      license_type: 'commercial',
      legal_form: 'Limited Liability Company - Single Owner (LLC - SO)',
    });
    expect(out.registration_number).toBeUndefined();
  });

  it('reads the number, dates and recognises the authority', () => {
    const out = parseTradeLicense(
      [
        'Sharjah Economic Development Department',
        'Trade Name : FATEMA CAKE SWEETS',
        'License No : 778179',
        'Register No : 193868',
        'Issue Date 02/11/2020',
        'Expiry Date 02/11/2027',
      ].join('\n'),
    );
    expect(out).toMatchObject({
      license_number: '778179',
      registration_number: '193868',
      issue_date: '2020-11-02',
      expiry_date: '2027-11-02',
      issuing_authority: 'sharjah_sedd',
      emirate: 'Sharjah',
      legal_name: 'FATEMA CAKE SWEETS',
    });
  });
});
