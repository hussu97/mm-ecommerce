import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Card } from './Card';

describe('Card', () => {
  it('renders children', () => {
    render(<Card>Hello Card</Card>);
    expect(screen.getByText('Hello Card')).toBeInTheDocument();
  });

  it('renders as a div', () => {
    render(<Card data-testid="card">Content</Card>);
    expect(screen.getByTestId('card').tagName).toBe('DIV');
  });

  it('applies md padding by default', () => {
    render(<Card data-testid="card">Content</Card>);
    expect(screen.getByTestId('card').className).toContain('p-5');
  });

  it('applies no padding when padding="none"', () => {
    render(<Card padding="none" data-testid="card">Content</Card>);
    const className = screen.getByTestId('card').className;
    expect(className).not.toContain('p-3');
    expect(className).not.toContain('p-5');
    expect(className).not.toContain('p-7');
  });

  it('applies sm padding', () => {
    render(<Card padding="sm" data-testid="card">Content</Card>);
    expect(screen.getByTestId('card').className).toContain('p-3');
  });

  it('applies lg padding', () => {
    render(<Card padding="lg" data-testid="card">Content</Card>);
    expect(screen.getByTestId('card').className).toContain('p-7');
  });

  it('adds hover shadow class when hover=true', () => {
    render(<Card hover data-testid="card">Content</Card>);
    expect(screen.getByTestId('card').className).toContain('hover:shadow-md');
  });

  it('does not add hover shadow class by default', () => {
    render(<Card data-testid="card">Content</Card>);
    expect(screen.getByTestId('card').className).not.toContain('hover:shadow-md');
  });

  it('merges custom className', () => {
    render(<Card className="custom-class" data-testid="card">Content</Card>);
    expect(screen.getByTestId('card').className).toContain('custom-class');
  });

  it('passes through HTML attributes', () => {
    render(<Card aria-label="product card">Content</Card>);
    expect(screen.getByLabelText('product card')).toBeInTheDocument();
  });
});
