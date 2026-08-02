import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { HomeEditor } from './HomeEditor';

vi.mock('@/lib/api', () => ({
  uploadsApi: { uploadImage: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

/**
 * The home page renders whatever this editor writes, so the contract that
 * matters is the shape of the JSON it hands back — not the markup.
 */
function setup(content: Record<string, unknown> = {}) {
  const onChange = vi.fn();
  render(<HomeEditor content={content} onChange={onChange} />);
  return { onChange, latest: () => onChange.mock.calls.at(-1)?.[0] };
}

const SEEDED = {
  hero: { autoplay_ms: 6000, slides: [{ headline: 'Cookie melts,', cta_href: '/cat-cookiemelt' }] },
  usps: { theme: 'plum', speed_s: 38, items: [{ icon: 'local_shipping', label: 'Delivered' }] },
  featured: { title: 'Bestsellers' },
  categories: { tiles: [{ slug: 'cat-brownies' }, { slug: 'cat-cookies' }] },
  promos: { items: [{ title: 'Soft in the middle.' }] },
  baker: { quote: 'Small batches.' },
  cater: { occasions: [{ icon: '🎉', label: 'Birthdays' }] },
};

describe('HomeEditor', () => {
  it('lists every home section for reordering, in render order', () => {
    setup();
    const labels = ['Hero carousel', 'Why-us strip', 'Bestsellers', 'Category tiles', 'Promo bands', 'Meet the baker', 'We cater to'];
    labels.forEach((label, i) => {
      expect(screen.getByText(`${i + 1}. ${label}`)).toBeInTheDocument();
    });
  });

  it('writes a full layout order the first time a section is moved', () => {
    const { latest } = setup();

    // Move "Why-us strip" (index 1) up.
    fireEvent.click(screen.getAllByTitle('Move up')[1]);

    expect(latest().layout.order).toEqual([
      'usps', 'hero', 'featured', 'categories', 'promos', 'baker', 'cater',
    ]);
  });

  it('hides a section without dropping it from the order', () => {
    const { latest } = setup(SEEDED);

    fireEvent.click(screen.getAllByTitle('Hide section')[4]); // Promo bands

    expect(latest().layout.hidden).toEqual(['promos']);
    expect(latest().layout.order).toContain('promos');
  });

  it('appends a blank hero slide pointing at the full catalogue', () => {
    const { latest } = setup(SEEDED);

    fireEvent.click(screen.getByText('Add slide'));

    expect(latest().hero.slides).toHaveLength(2);
    expect(latest().hero.slides[1]).toEqual({ cta_href: '/all-products' });
    // The existing slide is untouched.
    expect(latest().hero.slides[0].headline).toBe('Cookie melts,');
  });

  it('stores autoplay in milliseconds but asks for seconds', () => {
    const { latest } = setup(SEEDED);

    fireEvent.change(screen.getByLabelText('Seconds per slide'), { target: { value: '9' } });

    expect(latest().hero.autoplay_ms).toBe(9000);
  });

  it('keeps every other section intact when one field changes', () => {
    const { latest } = setup(SEEDED);

    fireEvent.change(screen.getByLabelText('Card badge'), { target: { value: 'New' } });

    const next = latest();
    expect(next.featured).toEqual({ title: 'Bestsellers', badge_text: 'New' });
    expect(next.usps).toEqual(SEEDED.usps);
    expect(next.cater).toEqual(SEEDED.cater);
  });

  it('removes the right repeated row', () => {
    const { latest } = setup(SEEDED);

    // Category tiles: drop the first of two.
    const tileRemove = screen.getAllByTitle('Remove');
    fireEvent.click(tileRemove[2]);

    expect(latest().categories.tiles).toEqual([{ slug: 'cat-cookies' }]);
  });

  it('offers the bundled banner artwork as image suggestions', () => {
    setup(SEEDED);
    expect(
      screen.getAllByRole('option', { hidden: true }).some(
        o => o.getAttribute('value') === '/images/banners/hero-cookie-melt.jpg',
      ),
    ).toBe(true);
  });
});
