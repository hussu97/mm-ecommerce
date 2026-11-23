import { describe, expect, it } from 'vitest';
import type { Employee } from '@/lib/people-api';
import type { LegalEntity } from '@/lib/pos-types';
import { profileFill } from './DocumentForm';

const entities = [{ id: 'le-1', legal_name: 'Fatema Cake Sweets', brand_name: 'Melting Moments Cakes' }] as LegalEntity[];
const blank = { legal_entity_id: null, sponsor_name: null } as unknown as Employee;

describe('profileFill — sponsor', () => {
  it('links a printed establishment that is one of our legal entities', () => {
    expect(profileFill(blank, { establishment: 'FATEMA CAKE SWEETS' }, entities)).toEqual({ legal_entity_id: 'le-1' });
  });

  it('keeps another company as the sponsor name', () => {
    expect(profileFill(blank, { establishment: 'Some Other Trading LLC' }, entities)).toEqual({
      sponsor_name: 'Some Other Trading LLC',
    });
  });

  it('leaves a sponsor already on file alone', () => {
    const held = { ...blank, sponsor_name: 'Some Other Trading LLC' } as Employee;
    expect(profileFill(held, { establishment: 'FATEMA CAKE SWEETS' }, entities)).toEqual({});
  });
});
