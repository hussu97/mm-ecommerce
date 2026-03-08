import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { Select } from './Select';

const options = [
  { value: 'ae', label: 'UAE' },
  { value: 'sa', label: 'Saudi Arabia' },
];

describe('Select', () => {
  it('renders a select element', () => {
    render(<Select options={options} />);
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('renders all provided options', () => {
    render(<Select options={options} />);
    expect(screen.getByRole('option', { name: 'UAE' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Saudi Arabia' })).toBeInTheDocument();
  });

  it('renders label when provided', () => {
    render(<Select options={options} label="Country" />);
    expect(screen.getByText('Country')).toBeInTheDocument();
  });

  it('associates label with select via htmlFor', () => {
    render(<Select options={options} label="Country" />);
    const label = screen.getByText('Country');
    const select = screen.getByRole('combobox');
    expect(label).toHaveAttribute('for', select.id);
  });

  it('renders placeholder as first option when provided', () => {
    render(<Select options={options} placeholder="Select a country" />);
    const first = screen.getByRole('option', { name: 'Select a country' });
    expect(first).toBeInTheDocument();
    expect(first).toHaveValue('');
  });

  it('shows error message when error prop is set', () => {
    render(<Select options={options} error="This field is required" />);
    expect(screen.getByText('This field is required')).toBeInTheDocument();
  });

  it('applies error border class when error prop is set', () => {
    render(<Select options={options} error="Invalid" />);
    expect(screen.getByRole('combobox').className).toContain('border-red-400');
  });

  it('does not render error message when error is not set', () => {
    render(<Select options={options} />);
    expect(screen.queryByRole('paragraph')).not.toBeInTheDocument();
  });

  it('is disabled when disabled prop is set', () => {
    render(<Select options={options} disabled />);
    expect(screen.getByRole('combobox')).toBeDisabled();
  });

  it('forwards ref to the select element', () => {
    const ref = createRef<HTMLSelectElement>();
    render(<Select options={options} ref={ref} />);
    expect(ref.current).toBeInstanceOf(HTMLSelectElement);
  });

  it('passes through HTML attributes', () => {
    render(<Select options={options} data-testid="my-select" />);
    expect(screen.getByTestId('my-select')).toBeInTheDocument();
  });

  it('applies custom className', () => {
    render(<Select options={options} className="custom-class" />);
    expect(screen.getByRole('combobox').className).toContain('custom-class');
  });
});
