import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QuantitySelector } from './QuantitySelector';

describe('QuantitySelector', () => {
  it('renders with the given value', () => {
    render(<QuantitySelector value={3} onChange={vi.fn()} />);
    const input = screen.getByRole('spinbutton');
    expect(input).toHaveValue(3);
  });

  it('calls onChange with incremented value when increment is clicked', () => {
    const onChange = vi.fn();
    render(<QuantitySelector value={3} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText('Increase quantity'));
    expect(onChange).toHaveBeenCalledWith(4);
  });

  it('calls onChange with decremented value when decrement is clicked', () => {
    const onChange = vi.fn();
    render(<QuantitySelector value={3} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText('Decrease quantity'));
    expect(onChange).toHaveBeenCalledWith(2);
  });

  it('decrement button is disabled at min value', () => {
    render(<QuantitySelector value={1} onChange={vi.fn()} min={1} />);
    expect(screen.getByLabelText('Decrease quantity')).toBeDisabled();
  });

  it('increment button is disabled at max value', () => {
    render(<QuantitySelector value={99} onChange={vi.fn()} max={99} />);
    expect(screen.getByLabelText('Increase quantity')).toBeDisabled();
  });

  it('does not call onChange when decrement is clicked at min', () => {
    const onChange = vi.fn();
    render(<QuantitySelector value={1} onChange={onChange} min={1} />);
    fireEvent.click(screen.getByLabelText('Decrease quantity'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('does not call onChange when increment is clicked at max', () => {
    const onChange = vi.fn();
    render(<QuantitySelector value={10} onChange={onChange} max={10} />);
    fireEvent.click(screen.getByLabelText('Increase quantity'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('calls onChange when a valid value is typed in input', () => {
    const onChange = vi.fn();
    render(<QuantitySelector value={3} onChange={onChange} min={1} max={99} />);
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '7' } });
    expect(onChange).toHaveBeenCalledWith(7);
  });

  it('does not call onChange when input value is out of range', () => {
    const onChange = vi.fn();
    render(<QuantitySelector value={3} onChange={onChange} min={1} max={99} />);
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '0' } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('disables both buttons and input when disabled prop is set', () => {
    render(<QuantitySelector value={3} onChange={vi.fn()} disabled />);
    expect(screen.getByLabelText('Decrease quantity')).toBeDisabled();
    expect(screen.getByLabelText('Increase quantity')).toBeDisabled();
    expect(screen.getByRole('spinbutton')).toBeDisabled();
  });

  it('renders aria-labels on both buttons', () => {
    render(<QuantitySelector value={3} onChange={vi.fn()} />);
    expect(screen.getByLabelText('Decrease quantity')).toBeInTheDocument();
    expect(screen.getByLabelText('Increase quantity')).toBeInTheDocument();
  });

  it('renders aria-label on the input', () => {
    render(<QuantitySelector value={3} onChange={vi.fn()} />);
    expect(screen.getByLabelText('Quantity')).toBeInTheDocument();
  });
});
