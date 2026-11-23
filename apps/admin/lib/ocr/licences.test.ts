import { describe, expect, it } from 'vitest';
import { parseDocument, parseDrivingLicense, parseVehicleLicense } from './parse';

// The layouts of a Dubai driving licence and mulkiya (front + back, read as
// one), with every identity value made up.
const DL = `United Arab Emirates الإمارات العربية المتحدة
Driving License رخصة قيادة
License No. 1234567
Name: Ravi Suresh Kumar
Nationality: India
Date of Birth: 15/03/1990
Issue Date: 10/01/2024
Expiry Date: 09/01/2034
Place of Issue: Dubai
Traffic Code No. 50123456
Permitted Vehicles
Light Vehicle Automatic   Motorcycle`;

const MULKIYA = `Vehicle License ملكية مركبة
Traffic Plate No. A 12345   Place of Issue: Dubai
T. C. No. 50123456
Owner: Ravi Suresh Kumar
Nationality: India
Exp. Date: 14/05/2026  Reg. Date: 15/05/2025
Ins. Exp.: 14/05/2026  Policy No. P/123/4567
Insurance Company: Orient Insurance
Model 2019   Veh. Type: Saloon
Make: Toyota Corolla
Color: White
Chassis No. JTDBR32E520012345
Engine No. 2ZR1234567`;

describe('driving licence', () => {
  it('reads the front and back', () => {
    const out = parseDrivingLicense(DL);
    expect(out).toMatchObject({
      document_number: '1234567',
      issue_date: '2024-01-10',
      expiry_date: '2034-01-09',
      issuing_authority: 'RTA Dubai',
      warnings: [],
    });
    expect(out.details).toEqual({
      name: 'Ravi Suresh Kumar',
      nationality: 'India',
      date_of_birth: '1990-03-15',
      traffic_code_number: '50123456',
      place_of_issue: 'Dubai',
      vehicle_classes: 'Light vehicle, Motorcycle',
    });
  });

  it('names the police as the authority outside Dubai', () => {
    expect(parseDrivingLicense(DL.replace('Place of Issue: Dubai', 'Place of Issue: Sharjah')).issuing_authority).toBe(
      'Sharjah Police',
    );
  });

  it('is reachable through parseDocument', () => {
    expect(parseDocument('driving_license', DL).document_number).toBe('1234567');
  });
});

describe('vehicle licence (mulkiya)', () => {
  it('reads the plate, dates and vehicle', () => {
    const out = parseVehicleLicense(MULKIYA);
    expect(out).toMatchObject({
      document_number: 'A 12345',
      issue_date: '2025-05-15',
      expiry_date: '2026-05-14',
      issuing_authority: 'RTA Dubai',
      warnings: [],
    });
    expect(out.details).toMatchObject({
      place_of_issue: 'Dubai',
      traffic_code_number: '50123456',
      owner: 'Ravi Suresh Kumar',
      insurance_expiry: '2026-05-14',
      insurance_policy: 'P/123/4567',
      insurer: 'Orient Insurance',
      model_year: '2019',
      vehicle_type: 'Saloon',
      make_model: 'Toyota Corolla',
      colour: 'White',
      chassis_number: 'JTDBR32E520012345',
      engine_number: '2ZR1234567',
    });
  });

  it('does not take the insurance expiry for the registration expiry', () => {
    const out = parseVehicleLicense(MULKIYA.replace('Ins. Exp.: 14/05/2026', 'Ins. Exp.: 20/06/2026'));
    expect(out.expiry_date).toBe('2026-05-14');
    expect(out.details.insurance_expiry).toBe('2026-06-20');
  });

  it('asks for the plate when it cannot read one', () => {
    const out = parseVehicleLicense(MULKIYA.replace('Traffic Plate No. A 12345', 'Traffic Plate No.'));
    expect(out.document_number).toBeUndefined();
    expect(out.warnings).toContain('No plate number found — enter it: it tells this vehicle apart.');
  });
});
