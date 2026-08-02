import { describe, it, expect } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { PhoneInput, isValidPhone } from './PhoneInput';

function Harness({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return <PhoneInput value={value} onChange={setValue} label="Phone" />;
}

describe('isValidPhone', () => {
  it('accepts a real UAE mobile in E.164', () => {
    expect(isValidPhone('+971501234567')).toBe(true);
  });

  it('rejects a number that is too short', () => {
    expect(isValidPhone('+9715')).toBe(false);
  });

  it('rejects an empty value', () => {
    expect(isValidPhone('')).toBe(false);
  });
});

describe('PhoneInput', () => {
  it('defaults to the UAE dial code', () => {
    render(<Harness />);
    expect(screen.getByText('+971')).toBeInTheDocument();
  });

  it('keeps the national digits in the field and the dial code in the picker', () => {
    render(<Harness />);
    const input = screen.getByLabelText('Phone');
    fireEvent.change(input, { target: { value: '501234567' } });
    // Grouping is whatever libphonenumber considers national format for the
    // country — asserting on digits keeps this test about our behaviour.
    expect((input as HTMLInputElement).value.replace(/\s/g, '')).toBe('501234567');
    expect((input as HTMLInputElement).value).not.toContain('+');
  });

  it('drops a leading zero rather than sending +9710…', () => {
    const { container } = render(<Harness />);
    const input = container.querySelector('input[type="tel"]')!;
    fireEvent.change(input, { target: { value: '0501234567' } });
    // Displayed nationally without the trunk zero.
    expect((input as HTMLInputElement).value.replace(/\s/g, '')).toBe('501234567');
  });

  it('switches country and strips the code when a full number is pasted', () => {
    const { container } = render(<Harness />);
    const input = container.querySelector('input[type="tel"]')!;
    fireEvent.change(input, { target: { value: '+447911123456' } });
    // The picker follows the pasted number to the UK — not to Guernsey, which
    // shares +44 and is what libphonenumber resolves this range to.
    expect(screen.getByText('+44')).toBeInTheDocument();
    expect(screen.getByLabelText('Country calling code')).toHaveValue('GB');
    // ...and the field keeps only the national part, not a doubled dial code.
    expect((input as HTMLInputElement).value).not.toContain('+44');
  });

  it('treats a 00-prefixed international number the same as +', () => {
    const { container } = render(<Harness />);
    const input = container.querySelector('input[type="tel"]')!;
    fireEvent.change(input, { target: { value: '00447911123456' } });
    expect(screen.getByText('+44')).toBeInTheDocument();
  });

  it('shows an existing E.164 value under its own country', () => {
    render(<Harness initial="+911234567890" />);
    expect(screen.getByText('+91')).toBeInTheDocument();
  });
});
