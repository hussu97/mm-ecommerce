import { NextResponse } from 'next/server';
import type { BlogPost, BlogPostListResponse, Category, Product, ProductListResponse } from '@/lib/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export const revalidate = 3600;

interface FaqItem { question: string; answer: string }

export async function GET() {
  // Fetch categories, products, FAQ, and blog posts in parallel
  const [categories, allProducts, faqItems, blogPosts] = await Promise.all([
    fetchCategories(),
    fetchAllProducts(),
    fetchFaq(),
    fetchBlogPosts(),
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
> Location: Sharjah, UAE — delivers to all Emirates

## Business Information

### Ordering
- Order online at ${SITE_URL}/en
- Order via WhatsApp: +971 50 368 7757 (message us with your order)
- No minimum order value

### Delivery
- Delivers to all UAE Emirates: Dubai, Sharjah, Ajman, Abu Dhabi, Fujairah, Ras Al Khaimah, Umm Al Quwain, Al Ain
- Delivery typically next day (orders placed before cutoff)
- Delivery fee varies by region

### Payment Methods
- Cash on delivery
- Credit/debit card (online checkout)

### Business Hours
- Monday to Saturday: 8:00 AM – 11:30 PM
- Sunday: 3:00 PM – 11:30 PM

### Returns & Refunds
- All sales are final — no returns or refunds on perishable baked goods
- If you receive a damaged or incorrect order, contact us within 24 hours via WhatsApp

### Pricing
- Price range: AED 15 – AED 200
- Currency: UAE Dirhams (AED)
- Prices shown include VAT`);

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

  // Blog posts
  if (blogPosts.length > 0) {
    sections.push('\n## Blog — Recent Articles\n');
    for (const post of blogPosts) {
      const c = post.content;
      let entry = `### ${c.title ?? post.slug}\n`;
      if (c.excerpt) entry += `${c.excerpt}\n`;
      entry += `- URL: ${SITE_URL}/en/blog/${post.slug}`;
      if (c.tags && c.tags.length > 0) entry += `\n- Tags: ${c.tags.join(', ')}`;
      sections.push(entry);
    }
  }

  return new NextResponse(sections.join('\n'), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}

async function fetchCategories(): Promise<Category[]> {
  try {
    const res = await fetch(`${API_BASE}/categories`, {
      next: { revalidate: 3600 },
      signal: AbortSignal.timeout(5000),
    });
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
        { next: { revalidate: 3600 }, signal: AbortSignal.timeout(5000) },
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

async function fetchBlogPosts(): Promise<BlogPost[]> {
  try {
    const res = await fetch(`${API_BASE}/blog/public?locale=en&per_page=50`, {
      next: { revalidate: 3600 },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return [];
    const data: BlogPostListResponse = await res.json();
    return data.items;
  } catch {
    return [];
  }
}

async function fetchFaq(): Promise<FaqItem[]> {
  try {
    const res = await fetch(`${API_BASE}/cms/public/faq?locale=en`, {
      next: { revalidate: 3600 },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.content?.items as FaqItem[]) ?? [];
  } catch {
    return [];
  }
}
