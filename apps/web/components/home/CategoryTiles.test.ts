import { describe, expect, it } from 'vitest';
import { resolveCategoryTiles, type CategoriesContent } from './CategoryTiles';
import type { Category } from '@/lib/types';

const category: Category = {
  id: 'brownies',
  name: 'Brownies',
  translations: {},
  slug: 'cat-brownies',
  reference: null,
  description: null,
  image_url: null,
  display_order: 1,
  is_active: true,
  product_count: 2,
};

describe('resolveCategoryTiles', () => {
  it('omits a CMS tile when its linked category is hidden', () => {
    const content: CategoriesContent = {
      tiles: [{ slug: 'cat-hidden', label: 'Hidden', href: '/special-offer' }],
    };

    expect(resolveCategoryTiles(content, [category], 'en')).toEqual([]);
  });

  it('keeps an explicitly standalone CMS tile', () => {
    const content: CategoriesContent = {
      tiles: [{ label: 'Catering', href: '/catering' }],
    };

    expect(resolveCategoryTiles(content, [category], 'en')).toMatchObject([
      { href: '/en/catering', label: 'Catering' },
    ]);
  });
});
