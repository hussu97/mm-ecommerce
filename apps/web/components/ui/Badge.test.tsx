import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Badge } from './Badge';

describe('Badge', () => {
  it('renders children', () => {
    render(<Badge>In Stock</Badge>);
    expect(screen.getByText('In Stock')).toBeInTheDocument();
  });

  it('renders as a span element', () => {
    const { container } = render(<Badge>Label</Badge>);
    expect(container.firstChild?.nodeName).toBe('SPAN');
  });

  it('applies primary variant class by default', () => {
    const { container } = render(<Badge>Label</Badge>);
    expect((container.firstChild as HTMLElement).className).toContain('bg-primary');
  });

  it('applies success variant class', () => {
    const { container } = render(<Badge variant="success">Active</Badge>);
    expect((container.firstChild as HTMLElement).className).toContain('bg-green-100');
    expect((container.firstChild as HTMLElement).className).toContain('text-green-800');
  });

  it('applies warning variant class', () => {
    const { container } = render(<Badge variant="warning">Pending</Badge>);
    expect((container.firstChild as HTMLElement).className).toContain('bg-yellow-100');
    expect((container.firstChild as HTMLElement).className).toContain('text-yellow-800');
  });

  it('applies error variant class', () => {
    const { container } = render(<Badge variant="error">Out of Stock</Badge>);
    expect((container.firstChild as HTMLElement).className).toContain('bg-red-100');
    expect((container.firstChild as HTMLElement).className).toContain('text-red-700');
  });

  it('applies secondary variant class', () => {
    const { container } = render(<Badge variant="secondary">Tag</Badge>);
    expect((container.firstChild as HTMLElement).className).toContain('bg-secondary');
  });

  it('applies outline variant class', () => {
    const { container } = render(<Badge variant="outline">New</Badge>);
    expect((container.firstChild as HTMLElement).className).toContain('border-primary');
    expect((container.firstChild as HTMLElement).className).toContain('bg-transparent');
  });

  it('merges custom className', () => {
    const { container } = render(<Badge className="custom-class">Label</Badge>);
    expect((container.firstChild as HTMLElement).className).toContain('custom-class');
  });

  it('renders complex children', () => {
    render(<Badge><span data-testid="inner">inner</span></Badge>);
    expect(screen.getByTestId('inner')).toBeInTheDocument();
  });
});
