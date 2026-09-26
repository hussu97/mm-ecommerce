import { describe, expect, it } from 'vitest';
import { linesError, newLine, toLinesIn } from './LinesEditor';
import { recipeError, toRecipeIn } from './RecipeEditor';
import { EMPTY_ADDRESS, toAddressIn } from './AddressPicker';
import { toCustomerIn } from './ContactFields';
import { paymentError, toPaymentFields } from './PaymentFields';
import { deliveryLabel } from './display';

describe('custom-order form helpers', () => {
  it('requires at least one titled, priced line and sends prices as typed strings', () => {
    expect(linesError([])).toMatch(/at least one/);
    expect(linesError([newLine({ unit_price: '10' })])).toMatch(/title/);
    expect(linesError([newLine({ title: 'Cake', unit_price: '' })])).toMatch(/price/);
    expect(linesError([newLine({ title: 'Cake', unit_price: '10.555' })])).toMatch(/price/);
    expect(linesError([newLine({ title: 'Cake', quantity: '0', unit_price: '10' })])).toMatch(/quantity/);

    const lines = [newLine({ title: ' Cake ', quantity: '2', unit_price: '250.50', notes: '  ' })];
    expect(linesError(lines)).toBeNull();
    // No arithmetic: the unit price reaches the API exactly as typed.
    expect(toLinesIn(lines)).toEqual([{ title: 'Cake', quantity: 2, unit_price: '250.50', notes: null }]);
  });

  it('allows a recipe above stock but not a zero or malformed quantity', () => {
    expect(recipeError([{ item_id: 'a', quantity: '0' }], [])).toMatch(/above zero/);
    expect(recipeError([{ item_id: 'a', quantity: '1.5.2' }], [])).toMatch(/above zero/);
    expect(recipeError([{ item_id: 'a', quantity: '999999' }], [])).toBeNull();
    expect(toRecipeIn([{ item_id: 'a', quantity: ' 12.5 ' }])).toEqual([{ item_id: 'a', quantity: '12.5' }]);
  });

  it('sends no address when nothing was entered, and a pin only when whole', () => {
    expect(toAddressIn(EMPTY_ADDRESS)).toBeNull();
    expect(toAddressIn({ ...EMPTY_ADDRESS, unit_number: 'Villa 4' })).toEqual({
      latitude: null,
      longitude: null,
      address_line_1: null,
      unit_number: 'Villa 4',
    });
    expect(toAddressIn({ ...EMPTY_ADDRESS, latitude: 25.3, longitude: 55.4 })).toMatchObject({
      latitude: 25.3,
      longitude: 55.4,
    });
  });

  it('sends blank contact fields as null', () => {
    expect(toCustomerIn({ name: ' Sara ', email: '', phone: ' ' })).toEqual({
      name: 'Sara',
      email: null,
      phone: null,
    });
  });

  it('asks for a fee choice only with a card, and drops it otherwise', () => {
    expect(paymentError('card', '')).toMatch(/card fee/);
    expect(paymentError('card', 'included')).toBeNull();
    expect(paymentError('', '')).toBeNull();
    expect(toPaymentFields('cash', 'included')).toEqual({ payment_type: 'cash', card_fee_mode: null });
    expect(toPaymentFields('', '')).toEqual({ payment_type: null, card_fee_mode: null });
    expect(toPaymentFields('card', 'separate_line')).toEqual({
      payment_type: 'card',
      card_fee_mode: 'separate_line',
    });
  });

  it('labels the delivery promise at the precision it was made', () => {
    expect(deliveryLabel(null, null)).toBe('—');
    expect(deliveryLabel('2026-09-30', null)).toContain('30');
    expect(deliveryLabel('2026-09-30', '16:00:00')).toMatch(/16:00$/);
  });
});
