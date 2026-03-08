import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Spinner } from './Spinner';

describe('Spinner', () => {
  it('renders with role="status"', () => {
    render(<Spinner />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('renders with aria-label="Loading"', () => {
    render(<Spinner />);
    expect(screen.getByLabelText('Loading')).toBeInTheDocument();
  });

  it('applies sm size classes', () => {
    render(<Spinner size="sm" />);
    const el = screen.getByRole('status');
    expect(el.className).toContain('w-4');
    expect(el.className).toContain('h-4');
  });

  it('applies md size classes by default', () => {
    render(<Spinner />);
    const el = screen.getByRole('status');
    expect(el.className).toContain('w-6');
    expect(el.className).toContain('h-6');
  });

  it('applies lg size classes', () => {
    render(<Spinner size="lg" />);
    const el = screen.getByRole('status');
    expect(el.className).toContain('w-8');
    expect(el.className).toContain('h-8');
  });

  it('merges custom className', () => {
    render(<Spinner className="text-primary" />);
    expect(screen.getByRole('status').className).toContain('text-primary');
  });

  it('renders the animate-spin class', () => {
    render(<Spinner />);
    expect(screen.getByRole('status').className).toContain('animate-spin');
  });
});
