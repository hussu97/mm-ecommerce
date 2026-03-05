import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { Button } from './Button';

describe('Button', () => {
  it('renders children', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByText('Click me')).toBeInTheDocument();
  });

  it('is a button element', () => {
    render(<Button>Click</Button>);
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('applies primary variant class by default', () => {
    render(<Button>Click</Button>);
    expect(screen.getByRole('button').className).toContain('bg-primary');
  });

  it('applies secondary variant class', () => {
    render(<Button variant="secondary">Click</Button>);
    expect(screen.getByRole('button').className).toContain('bg-secondary');
  });

  it('applies ghost variant class', () => {
    render(<Button variant="ghost">Click</Button>);
    expect(screen.getByRole('button').className).toContain('bg-transparent');
  });

  it('applies sm size tracking class', () => {
    render(<Button size="sm">Click</Button>);
    expect(screen.getByRole('button').className).toContain('px-3');
  });

  it('applies lg size tracking class', () => {
    render(<Button size="lg">Click</Button>);
    expect(screen.getByRole('button').className).toContain('px-7');
  });

  it('is disabled when loading', () => {
    render(<Button loading>Click</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('is disabled when disabled prop is set', () => {
    render(<Button disabled>Click</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('applies w-full when fullWidth is true', () => {
    render(<Button fullWidth>Click</Button>);
    expect(screen.getByRole('button').className).toContain('w-full');
  });

  it('forwards ref to button element', () => {
    const ref = createRef<HTMLButtonElement>();
    render(<Button ref={ref}>Click</Button>);
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
  });

  it('passes through HTML attributes', () => {
    render(<Button data-testid="my-btn">Click</Button>);
    expect(screen.getByTestId('my-btn')).toBeInTheDocument();
  });
});
