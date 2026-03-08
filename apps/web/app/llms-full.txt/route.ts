import { NextResponse } from 'next/server';
import type { Category, Product, ProductListResponse } from '@/lib/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const revalidate = 3600;

interface FaqItem { question: string; answer: string }

export async function GET() {
  // Fetch categories, all products, and FAQ content in parallel
  const [categories, allProducts, faqItems] = await Promise.all([
    fetchCategories(),
    fetchAllProducts(),
    fetchFaq(),
  ]);

  // Group products by category
  const categoryMap = new Map<string, { category: Category; products: Product[] }>();
  for (const cat of categories.filter(c => c.is_active)) {
    categoryMap.set(cat.id, { category: cat, products: [] });
  }
  for (const p of allProducts) {
    if (p.category_id && categoryMap.has(p.category_id)) {
      categoryMap.get(p.category_id)!.products.push(p);
    }
  }

  const sections: string[] = [];

  // Header
  sections.push(`# Melting Moments Cakes — Full Product Catalogue

> Artisanal bakery in the UAE delivering handcrafted brownies, cookies, cookie melts, and desserts across Dubai, Sharjah, and all Emirates. Founded by Fatema Abbasi.
> Website: ${SITE_URL}
> WhatsApp: +971 50 368 7757
> Instagram: @meltingmomentscakes
> Location: Sharjah, UAE — delivers to all Emirates`);

  // Products by category
  for (const { category, products } of categoryMap.values()) {
    if (products.length === 0) continue;
    sections.push(`\n## ${category.name}\n`);
    if (category.description) sections.push(category.description);
    sections.push('');
    for (const p of products) {
      let line = `### ${p.name}\n`;
      line += `- Price: AED ${Number(p.base_price).toFixed(2)}\n`;
      if (p.calories) line += `- Calories: ${p.calories} cal\n`;
      if (p.sku) line += `- SKU: ${p.sku}\n`;
      if (p.description) line += `- Description: ${p.description}\n`;
      line += `- URL: ${SITE_URL}/en/${category.slug}/${p.slug}`;
      sections.push(line);
    }
  }

  // FAQ
  if (faqItems.length > 0) {
    sections.push('\n## Frequently Asked Questions\n');
    for (const { question, answer } of faqItems) {
      sections.push(`**Q: ${question}**\nA: ${answer}\n`);
    }
  }

  return new NextResponse(sections.join('\n'), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}

async function fetchCategories(): Promise<Category[]> {
  try {
    const res = await fetch(`${API_BASE}/categories`, { next: { revalidate: 3600 } });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

async function fetchAllProducts(): Promise<Product[]> {
  const all: Product[] = [];
  let page = 1;
  let hasMore = true;
  try {
    while (hasMore) {
      const res = await fetch(
        `${API_BASE}/products?per_page=100&page=${page}&is_active=true`,
        { next: { revalidate: 3600 } },
      );
      if (!res.ok) break;
      const data: ProductListResponse = await res.json();
      all.push(...data.items);
      hasMore = page < data.pages;
      page++;
    }
  } catch {
    // return what we have
  }
  return all;
}

async function fetchFaq(): Promise<FaqItem[]> {
  try {
    const res = await fetch(`${API_BASE}/cms/public/faq?locale=en`, { next: { revalidate: 3600 } });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.content?.items as FaqItem[]) ?? [];
  } catch {
    return [];
  }
}
