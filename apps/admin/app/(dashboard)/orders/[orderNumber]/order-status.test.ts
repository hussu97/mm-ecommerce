/**
 * The address panel renders every order type from one snapshot, whatever shape
 * the source used. A promoted marketplace order carries the marketplace's own
 * keys, not the website `unit_number`/`address_line_1` set — and the old fixed
 * key list matched none of them, so the whole address (and the map pin) rendered
 * blank on the one screen a dispatcher opens when a rider can't find the door.
 */

import { describe, expect, it } from 'vitest';
import { normalizeAddressSnapshot } from './order-status';

describe('normalizeAddressSnapshot', () => {
  it('reads the website canonical shape', () => {
    const out = normalizeAddressSnapshot({
      unit_number: 'Apt G32',
      address_line_1: 'Golden Sands',
      city: 'Dubai',
      latitude: '25.0015905',
      longitude: '55.155784',
    });
    expect(out.rows).toEqual([
      { label: 'Flat / villa / office', value: 'Apt G32' },
      { label: 'Address', value: 'Golden Sands' },
      { label: 'City', value: 'Dubai' },
    ]);
    expect(out.lat).toBeCloseTo(25.0015905);
    expect(out.lng).toBeCloseTo(55.155784);
  });

  it('reads the Keeta shape {address, building, unit, house}', () => {
    const out = normalizeAddressSnapshot({
      address: 'Ayat Tower, Al Barsha',
      building: 'Ayat Tower',
      unit: '18',
      house: '18, 1801',
    });
    expect(out.rows).toEqual([
      { label: 'Flat / villa / office', value: '18' },
      { label: 'Address', value: 'Ayat Tower, Al Barsha' },
      { label: 'Building / directions', value: 'Ayat Tower' },
    ]);
  });

  it('reads the Noon shape and un-scales its integer coordinates', () => {
    const out = normalizeAddressSnapshot({
      street: '606 Burj Al Alam',
      area: 'Al Majaz 3 - Sharjah',
      city: 'Sharjah',
      lat: '253337438',
      lng: '553719212',
    });
    expect(out.rows).toEqual([
      { label: 'Address', value: '606 Burj Al Alam' },
      { label: 'Area', value: 'Al Majaz 3 - Sharjah' },
      { label: 'City', value: 'Sharjah' },
    ]);
    // 253337438 → 25.3337438 (a real UAE lat, not a raw scaled int on the map).
    expect(out.lat).toBeCloseTo(25.3337438);
    expect(out.lng).toBeCloseTo(55.3719212);
  });

  it('reads the Careem shape with decimal coordinates left as-is', () => {
    const out = normalizeAddressSnapshot({
      street: 'JBR',
      building: 'Al Fattan marine tower',
      number: '3701 - Floor 37',
      city: 'Dubai',
      area: 'Dubai Marina',
      nickname: 'Home',
      lat: 25.078814,
      lng: 55.136062,
    });
    expect(out.rows).toEqual([
      { label: 'Flat / villa / office', value: '3701 - Floor 37' },
      { label: 'Address', value: 'JBR' },
      { label: 'Building / directions', value: 'Al Fattan marine tower' },
      { label: 'Area', value: 'Dubai Marina' },
      { label: 'City', value: 'Dubai' },
      { label: 'Saved as', value: 'Home' },
    ]);
    expect(out.lat).toBeCloseTo(25.078814);
    expect(out.lng).toBeCloseTo(55.136062);
  });

  it('is empty and coordinate-free for a null or shapeless snapshot', () => {
    expect(normalizeAddressSnapshot(null)).toEqual({ rows: [], lat: null, lng: null });
    expect(normalizeAddressSnapshot({ foo: 'bar' })).toEqual({
      rows: [],
      lat: null,
      lng: null,
    });
    // A zeroed pin is "the middle of the sea" — treated as no pin.
    expect(normalizeAddressSnapshot({ lat: 0, lng: 0 }).lat).toBeNull();
  });
});
