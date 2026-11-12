import { readFileSync } from 'fs';
import { join } from 'path';
import { describe, expect, it } from 'vitest';
import { textFromPdfItems } from './extract';
import {
  findDates,
  mrzCheckDigit,
  mrzNames,
  parseLabourCard,
  parseEmiratesId,
  parseMedicalInsurance,
  parseMrz,
  parseOhc,
  parseTd1,
  parsePassport,
  parseTradeLicense,
  parseVisa,
  parseWpsCard,
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

// A made-up resident: ID 784-1990-1234567-1, card 123456789, born 15 Jan 1990,
// card valid 11 Mar 2026 → 10 Mar 2028. The MRZ's check digits are real.
const EID_MRZ = [
  'ILARE1234567897784199012345671',
  '9001158M2803102PHL<<<<<<<<<<<3',
  'CRUZ<<JUAN<SANTOS<<<<<<<<<<<<<',
].join('\n');

const EID_FRONT = `United Arab Emirates الإمارات العربية المتحدة
Resident Identity Card بطاقة هوية مقيم
ID Number / رقم الهوية
784-1990-1234567-1
Name: Juan Santos Cruz الاسم
Date of Birth: 15/01/1990 تاريخ الميلاد
Nationality: Philippines الجنسية
Issuing Date: 11/03/2026 تاريخ الإصدار
Expiry Date: 10/03/2028 تاريخ الانتهاء
Sex: M الجنس`;

const EID_BACK = `Occupation: Baker المهنة
Employer: Fatema Cake Sweets صاحب العمل
Issuing Place: Dubai مكان الإصدار
Card Number / رقم البطاقة 123456789
${EID_MRZ}`;

describe('Emirates ID', () => {
  it('reads both sides, with the MRZ check digits agreeing', () => {
    const out = parseEmiratesId(`${EID_FRONT}\n\n${EID_BACK}`);
    expect(out.document_number).toBe('784-1990-1234567-1');
    expect(out.issue_date).toBe('2026-03-11');
    expect(out.expiry_date).toBe('2028-03-10');
    expect(out.issuing_authority).toMatch(/Identity, Citizenship/);
    expect(out.details).toEqual({
      card_number: '123456789',
      name: 'Juan Santos Cruz',
      nationality: 'Philippines',
      date_of_birth: '1990-01-15',
      sex: 'M',
      occupation: 'Baker',
      employer: 'Fatema Cake Sweets',
      place_of_issue: 'Dubai',
    });
    expect(out.person).toMatchObject({
      full_name: 'Juan Santos Cruz',
      nationality: 'Philippines',
      date_of_birth: '1990-01-15',
      gender: 'M',
      job_title: 'Baker',
      establishment: 'Fatema Cake Sweets',
    });
    expect(out.warnings).toEqual([]);
  });

  it('keeps issue and expiry apart when the card prints them side by side', () => {
    // Real Tesseract output of a rendered card: both dates on one line.
    const out = parseEmiratesId(
      'Date of Birth: 15/01/1990\nIssuing Date: 11/03/2026   Expiry Date: 10/03/2028\nSex: M',
    );
    expect(out.issue_date).toBe('2026-03-11');
    expect(out.expiry_date).toBe('2028-03-10');
    expect(out.details.date_of_birth).toBe('1990-01-15');
  });

  it('reads the front alone, and asks for the back', () => {
    const out = parseEmiratesId(EID_FRONT);
    expect(out.document_number).toBe('784-1990-1234567-1');
    expect(out.expiry_date).toBe('2028-03-10');
    expect(out.issue_date).toBe('2026-03-11');
    expect(out.details.date_of_birth).toBe('1990-01-15');
    expect(out.details.card_number).toBeUndefined();
    expect(out.warnings.join(' ')).toMatch(/No machine-readable zone/);
  });

  it('decodes TD1 check digits, including the composite over the ID number', () => {
    const td1 = parseTd1(EID_MRZ);
    expect(td1).toMatchObject({
      cardNumber: '123456789',
      cardOk: true,
      idNumber: '784199012345671',
      compositeOk: true,
      birth: '1990-01-15',
      birthOk: true,
      expiry: '2028-03-10',
      expiryOk: true,
      sex: 'M',
      nationality: 'Philippines',
      surname: 'CRUZ',
      given: 'JUAN SANTOS',
      failures: 0,
    });
    expect(mrzCheckDigit('123456789')).toBe(7);
  });

  it('takes the Tesseract pass whose check digits pass, and repairs the names', () => {
    // Front reads as Latin noise; two passes over the back: the first misreads
    // the expiry, both turn the name filler into K/L. Spaces and O-for-0 too.
    const tesseract = `Ulnitcd Arab Emiratcs ioJl Kl
ILARE 123456789 7 784199012345671
9OO1158M2803192PHL<<<<<<<<<<<3
CRUZ<<JUAN<SANTOS<KKLKKKKKKKKK

ILARE1234567897784199012345671
9001158M2803102PHL<<<<<<<<<<<3
CRUZ<<JUAN<SANTOSKLLLLLLLLLLLL`;
    const out = parseEmiratesId(tesseract);
    expect(out.document_number).toBe('784-1990-1234567-1');
    expect(out.expiry_date).toBe('2028-03-10');
    expect(out.details.card_number).toBe('123456789');
    expect(out.details.name).toBe('Juan Santos Cruz');
    expect(out.details.date_of_birth).toBe('1990-01-15');
    expect(out.details.nationality).toBe('Philippines');
    expect(out.issue_date).toBeUndefined();
    expect(out.warnings.join(' ')).toMatch(/name in the machine-readable zone was hard to read/);
    expect(out.warnings.join(' ')).not.toMatch(/failed/);
  });

  it('falls back to the printed date when the MRZ one fails its check', () => {
    // Real Tesseract misread of a rendered card: 9001158 → 0001158.
    const misread = EID_MRZ.replace('9001158', '0001158');
    const out = parseEmiratesId(`${EID_FRONT}\n${EID_BACK.replace(EID_MRZ, misread)}`);
    expect(out.details.date_of_birth).toBe('1990-01-15');
    expect(out.document_number).toBe('784-1990-1234567-1');
    expect(out.warnings).toEqual([]);
  });

  it('warns when an MRZ value that failed its check is all there is', () => {
    // The back alone: the new card prints no expiry there, only the MRZ.
    const out = parseEmiratesId(EID_BACK.replace('2803102', '2808102'));
    expect(out.expiry_date).toBe('2028-08-10');
    expect(out.warnings.join(' ')).toMatch(/The expiry date failed the machine-readable zone check/);
    expect(out.warnings.join(' ')).not.toMatch(/ID number|card number/);
  });

  it('flags an ID number the front and the MRZ disagree on', () => {
    const out = parseEmiratesId(`${EID_FRONT.replace('1234567-1', '1234561-1')}\n${EID_MRZ}`);
    // The MRZ's composite check passes, so its number wins.
    expect(out.document_number).toBe('784-1990-1234567-1');
    expect(out.warnings.join(' ')).toMatch(/ID number on the front and in the machine-readable zone differ/);
  });
});

describe('medical insurance card', () => {
  // A TPA-run card (NAS layout): dates side by side, insurer named by label.
  const nas = `NAS Neuron
Insurer: Orient Insurance
Member Name: JUAN SANTOS CRUZ
Member ID: 1234 5678 9012
Policy No: P/2026/004512
Network: RN3
Category: C
Date of Birth: 15/01/1990   Gender: Male
Valid From: 01/04/2026   Valid To: 31/03/2027`;

  it('reads the member and policy numbers, insurer, TPA, network and dates', () => {
    const out = parseMedicalInsurance(nas);
    expect(out.document_number).toBe('1234 5678 9012');
    expect(out.issuing_authority).toBe('Orient Insurance');
    expect(out.details).toEqual({
      policy_number: 'P/2026/004512',
      tpa: 'NAS',
      network: 'RN3',
      category: 'C',
      name: 'Juan Santos Cruz',
    });
    // Side by side on one line, and the birth date is not the issue date.
    expect(out.issue_date).toBe('2026-04-01');
    expect(out.expiry_date).toBe('2027-03-31');
    expect(out.person).toMatchObject({
      full_name: 'Juan Santos Cruz',
      date_of_birth: '1990-01-15',
      gender: 'M',
    });
    expect(out.warnings).toEqual([]);
  });

  it('knows the insurer by its name when nothing labels it', () => {
    const out = parseMedicalInsurance(`DAMAN National Health Insurance Company
Card No: 1-234-5678901-02
Valid Until: 31/12/2027`);
    expect(out.issuing_authority).toBe('Daman');
    expect(out.document_number).toBe('1-234-5678901-02');
    expect(out.expiry_date).toBe('2027-12-31');
    expect(out.details.tpa).toBeUndefined();
  });

  it('says so when it has to guess the expiry', () => {
    const out = parseMedicalInsurance('Orient Insurance\n01/04/2026 31/03/2027');
    expect(out.expiry_date).toBe('2027-03-31');
    expect(out.warnings.join(' ')).toMatch(/guessed/);
  });
});

describe('WPS salary card', () => {
  const ratibi = `ratibi
FAB
5368 9812 3456 7890
VALID THRU 08/28
JUAN SANTOS CRUZ
Employee ID: 30400000000001`;

  it('keeps only the last four digits of the card number', () => {
    const out = parseWpsCard(ratibi);
    expect(out.document_number).toBe('7890');
    expect(JSON.stringify(out)).not.toMatch(/5368/);
  });

  it('reads valid-thru as the last day of that month, and the provider', () => {
    const out = parseWpsCard(ratibi);
    expect(out.expiry_date).toBe('2028-08-31');
    expect(out.issuing_authority).toBe('First Abu Dhabi Bank (Ratibi)');
    expect(out.details.name).toBe('Juan Santos Cruz');
    expect(out.details.employee_code).toBe('30400000000001');
    expect(out.warnings).toEqual([]);
  });

  it('handles February and a masked number', () => {
    const out = parseWpsCard('C3Pay\nXXXX XXXX XXXX 4321\nGOOD THRU 02/28');
    expect(out.document_number).toBe('4321');
    expect(out.expiry_date).toBe('2028-02-29');
    expect(out.issuing_authority).toBe('C3Pay');
  });

  it('picks the embossed name that matches the employee on file', () => {
    const out = parseWpsCard('MASTERCARD\nAL ANSARI EXCHANGE\nMARIA REYES\nSAMPLE TEXT', {
      knownName: 'Maria Lourdes Reyes',
    });
    expect(out.details.name).toBe('Maria Reyes');
    expect(out.issuing_authority).toBe('Al Ansari Exchange');
  });

  it('leaves the name empty when several lines could be it', () => {
    const out = parseWpsCard('MARIA REYES\nSAMPLE TEXT');
    expect(out.details.name).toBeUndefined();
  });

  it('flags an unlabelled month/year and reads an IBAN', () => {
    const out = parseWpsCard('Emirates NBD\n1234 5678 9012 3456\n11/29\nAE07 0331 2345 6789 0123 456');
    expect(out.expiry_date).toBe('2029-11-30');
    expect(out.details.account_number).toBe('AE070331234567890123456');
    expect(out.warnings.join(' ')).toMatch(/unlabelled month\/year/);
  });
});
