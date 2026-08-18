/**
 * Drives every admin route in a phone viewport and reports what would be
 * unusable there.
 *
 * The console is used from a phone far more than it was designed for, and the
 * failures are the kind nobody notices at a desk: a table that scrolls sideways
 * inside its own box, a control small enough that iOS zooms the page on focus
 * and never zooms back, a link 16px tall. See
 * `docs/admin-mobile-design-system.md` for the rules this measures.
 *
 * Fixtures come from the API's own OpenAPI document rather than a database, so
 * this needs nothing running but the admin dev server — and every list comes
 * back full, with strings long enough to be worth testing against.
 *
 *   pnpm --filter admin dev            # port 3100, in one shell
 *   pnpm --filter admin mobile-audit   # in another
 *   pnpm --filter admin mobile-audit -- 1280   # and the desktop shape
 *
 * Exits non-zero if any route fails, so it can gate a change.
 */

import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// Fixtures are derived from the API's committed OpenAPI document rather than
// stored beside this script. A second copy would go stale the first time a
// response model changed, and a stale fixture makes a screen render a shape it
// never receives — which is worse than not testing it.
const SPEC = JSON.parse(
  fs.readFileSync(path.join(HERE, '../../../packages/types/openapi.json'), 'utf8'),
);

// Long enough to be worth testing against. A fixture of "string" proves a
// table fits; a real product name is what actually broke the layout.
const LONG = {
  name: 'Chocolate Fudge Celebration Cake with Salted Caramel',
  email: 'a.very.long.customer.address@somelongdomainname.example.com',
  title: 'Chocolate Fudge Celebration Cake with Salted Caramel',
  description: 'A long-ish description that would realistically appear in this column and has to wrap.',
  label: 'Northern Emirates evening run',
  reference: 'K001-SHARJAH-CENTRAL',
  order_number: 'MM-20260818-00147',
  message: 'Something went wrong contacting the courier and this is the sentence it returned.',
  address: 'Shop 4, Al Majaz Waterfront, Buhairah Corniche, Sharjah, United Arab Emirates',
  slug: 'chocolate-fudge-celebration-cake',
  code: 'WELCOME20-LONGCODE',
  phone: '+971 55 123 4567',
  customer_name: 'Abdulrahman Al-Maktoum',
  user_agent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
};

function refName(schema) {
  return schema.$ref ? schema.$ref.split('/').pop() : null;
}

function example(schema, key = '', depth = 0, seen = new Set()) {
  if (!schema) return null;
  const ref = refName(schema);
  if (ref) {
    if (seen.has(ref) || depth > 7) return null;
    return example(SPEC.components.schemas[ref], key, depth + 1, new Set([...seen, ref]));
  }
  for (const comb of ['anyOf', 'oneOf', 'allOf']) {
    if (schema[comb]) {
      const opts = schema[comb].filter(s => s.type !== 'null');
      return opts.length ? example(opts[0], key, depth + 1, seen) : null;
    }
  }
  if ('const' in schema) return schema.const;
  if (schema.enum) return schema.enum[0];
  if (schema.type === 'array') {
    const item = schema.items || {};
    const r = refName(item);
    // A self-referential array (a tree's children) terminates as empty, never
    // as null — a component that maps over it must still get a list.
    if (depth > 4 || (r && seen.has(r))) return [];
    return Array.from({ length: 6 }, () => example(item, key, depth + 1, seen));
  }
  if (schema.type === 'object' || schema.properties) {
    if (depth > 7) return {};
    const out = {};
    for (const [k, v] of Object.entries(schema.properties || {})) {
      out[k] = example(v, k, depth + 1, seen);
    }
    return out;
  }
  if (schema.type === 'boolean') return true;
  if (schema.type === 'integer') return 12;
  if (schema.type === 'number') return 149.5;
  if (schema.format === 'uuid') return `00000000-0000-4000-8000-${String(counter++).padStart(12, '0')}`;
  if (schema.format === 'date-time') return '2026-08-18T09:30:00+00:00';
  if (schema.format === 'date') return '2026-08-18';
  for (const [hint, value] of Object.entries(LONG)) if (key.includes(hint)) return value;
  if (key.endsWith('_at')) return '2026-08-18T09:30:00+00:00';
  if (key.endsWith('_date')) return '2026-08-18';
  if (key.endsWith('_url')) return 'https://example.com/images/cake.jpg';
  if (key.endsWith('_id')) return '00000000-0000-4000-8000-000000000001';
  return 'Sample value';
}

let counter = 1;
const FIXTURES = {};
for (const [route, ops] of Object.entries(SPEC.paths)) {
  const schema = ops.get?.responses?.['200']?.content?.['application/json']?.schema;
  if (schema) FIXTURES[route] = example(schema);
}
const BASE = process.env.ADMIN_URL || 'http://localhost:3100';
const WIDTH = Number(process.argv[2] || 390);
// Touch minimums and the iOS focus-zoom rule are properties of a phone, not of
// a narrow window: the console's compact type scale is deliberate at a desk and
// flagging it there would make the desktop run noise. Above the `md` breakpoint
// only the no-sideways-scroll rule is enforced.
const TOUCH = WIDTH < 768;
const OUT = process.argv[3] || path.join(HERE, `../.mobile-audit/${WIDTH}`);
fs.mkdirSync(OUT, { recursive: true });

// Match a concrete request path against the OpenAPI templates.
const TEMPLATES = Object.keys(FIXTURES).map(t => ({
  t,
  re: new RegExp('^' + t.replace(/\{[^}]+\}/g, '[^/]+') + '$'),
  specificity: (t.match(/\{/g) || []).length,
}));
TEMPLATES.sort((a, b) => a.specificity - b.specificity);

const ME = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'hussain@meltingmomentscakes.com',
  is_admin: true, is_active: true, is_guest: false,
  first_name: 'Hussain', last_name: 'Abbasi', phone: null,
  role_id: null, permissions: [], branch_ids: [],
  created_at: '2026-01-01T00:00:00+00:00', updated_at: '2026-01-01T00:00:00+00:00',
};

const ROUTES = [
  '/', '/products', '/products/new', '/categories', '/modifiers', '/menu-groups',
  '/branches', '/staff', '/devices', '/pos-config', '/pos-reports',
  '/inventory', '/purchase-orders',
  '/orders', '/orders/MM-20260818-00147', '/custom-orders', '/promo-codes', '/customers',
  '/delivery-zones', '/payment-gateways', '/analytics', '/content', '/languages',
  '/translations', '/admin-users', '/import', '/security', '/email-logs',
  '/webhook-logs', '/audit-logs', '/login',
];

// `CHROMIUM_PATH` for an environment whose browser build does not match the
// pinned Playwright version; otherwise Playwright's own download.
const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const ctx = await browser.newContext({
  viewport: { width: WIDTH, height: 844 },
  deviceScaleFactor: 2,
  isMobile: WIDTH < 700,
  hasTouch: WIDTH < 700,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
});

await ctx.route('**/api/v1/**', async route => {
  const url = new URL(route.request().url());
  const p = url.pathname;
  if (p.endsWith('/auth/me')) return route.fulfill({ json: ME });
  const hit = TEMPLATES.find(x => x.re.test(p));
  if (hit && FIXTURES[hit.t] !== null && FIXTURES[hit.t] !== undefined) {
    return route.fulfill({ json: FIXTURES[hit.t] });
  }
  return route.fulfill({ status: 200, json: [] });
});
// Fonts / icons are remote; let them fail fast rather than hang the page.
// fonts load normally so material-icons ligatures render

const results = [];
for (const r of ROUTES) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
  try {
    await page.goto(BASE + r, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2500);
    const info = await page.evaluate(() => {
      const de = document.documentElement;
      const over = [];
      const vw = de.clientWidth;
      for (const el of document.querySelectorAll('body *')) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        // An element sticking out past the viewport, that isn't inside a
        // deliberate horizontal scroller.
        if (rect.right > vw + 1 || rect.left < -1) {
          let scroller = null;
          for (let p = el.parentElement; p; p = p.parentElement) {
            const ox = getComputedStyle(p).overflowX;
            if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') { scroller = p; break; }
          }
          if (scroller) continue;
          over.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className && el.className.baseVal !== undefined ? el.className.baseVal : String(el.className || '')).slice(0, 140),
            right: Math.round(rect.right),
            text: (el.textContent || '').trim().slice(0, 60),
          });
        }
      }
      // Deliberate horizontal scrollers — a table you must drag sideways is
      // not "overflowing" but is exactly the awkwardness being audited.
      const scrollers = [];
      for (const el of document.querySelectorAll('*')) {
        const ox = getComputedStyle(el).overflowX;
        if (ox !== 'auto' && ox !== 'scroll') continue;
        if (el.scrollWidth <= el.clientWidth + 1) continue;
        // A scroller that says it means it — a tab strip, a chart, a payload
        // dump. Everything else sideways on a phone is a defect.
        if (el.closest('[data-scroll-intent]')) continue;
        scrollers.push({
          tag: el.tagName.toLowerCase(),
          cls: String(el.className || '').slice(0, 60),
          content: el.scrollWidth,
          visible: el.clientWidth,
          hiddenPct: Math.round((1 - el.clientWidth / el.scrollWidth) * 100),
        });
      }

      // Tap targets that are too small to hit reliably.
      const small = [];
      for (const el of document.querySelectorAll('button, a[href], select, input[type=checkbox], [role=button]')) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        const isBox = el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio');
        const minH = isBox ? 20 : 36;
        const minW = isBox ? 20 : 28;
        if (rect.height < minH || rect.width < minW) {
          small.push({
            tag: el.tagName.toLowerCase(),
            h: Math.round(rect.height), w: Math.round(rect.width),
            text: (el.textContent || '').trim().slice(0, 34),
          });
        }
      }
      // Controls small enough that iOS Safari zooms the page on focus.
      const zoomy = [];
      for (const el of document.querySelectorAll('input, select, textarea')) {
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs && fs < 16 && el.type !== 'checkbox' && el.type !== 'radio') {
          zoomy.push({ tag: el.tagName.toLowerCase(), fs, name: el.name || el.placeholder || '' });
        }
      }
      return {
        scrollW: de.scrollWidth, clientW: vw,
        overflow: over.slice(0, 12),
        overflowCount: over.length,
        scrollers,
        smallTargets: small.length,
        smallSample: small.slice(0, 6),
        zoomyInputs: zoomy.length,
        bodyText: (document.body.innerText || '').slice(0, 120).replace(/\n/g, ' | '),
      };
    });
    const name = r === '/' ? 'dashboard' : r.replace(/^\//, '').replace(/\//g, '_');
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
    results.push({ route: r, ...info, errors: errors.slice(0, 2) });
  } catch (e) {
    results.push({ route: r, error: String(e).slice(0, 200) });
  }
  await page.close();
}

await browser.close();
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(results, null, 2));

console.log(
  `\n=== ${WIDTH}px — ${results.length} routes — ` +
    `${TOUCH ? 'scroll + touch + zoom' : 'scroll only (desktop)'} ===`,
);
let failures = 0;
for (const r of results) {
  if (r.error) { failures++; console.log(`${r.route.padEnd(28)} ERROR ${r.error}`); continue; }
  const broken =
    (r.scrollers || []).length ||
    r.scrollW > r.clientW + 1 ||
    (TOUCH && (r.smallTargets || r.zoomyInputs));
  if (broken) failures++;
  const hs = r.scrollW > r.clientW + 1 ? `H-SCROLL ${r.scrollW}>${r.clientW}` : 'ok      ';
  const worst = (r.scrollers || []).sort((a, b) => b.hiddenPct - a.hiddenPct)[0];
  console.log(
    `${r.route.padEnd(26)} ${hs} sideways:${String(worst ? worst.hiddenPct + '%' : '-').padStart(4)} (${(r.scrollers || []).length})` +
      (TOUCH
        ? ` tap<36:${String(r.smallTargets).padStart(3)} zoom:${String(r.zoomyInputs).padStart(2)}`
        : ''),
  );
  for (const o of (r.overflow || []).slice(0, 3)) {
    console.log(`      ↳ ${o.tag} right=${o.right} "${o.text}" [${o.cls}]`);
  }
  if (r.errors?.length) console.log(`      !! ${r.errors[0]}`);
  for (const sc of (r.scrollers || []).slice(0, 2)) {
    console.log(`      ↳ scrolls sideways: ${sc.content}px of content in ${sc.visible}px [${sc.cls}]`);
  }
  for (const t of (TOUCH ? r.smallSample || [] : []).slice(0, 3)) {
    console.log(`      ↳ ${t.tag} ${t.w}x${t.h} "${t.text}"`);
  }
}

console.log(`\nScreenshots: ${OUT}`);
if (failures) {
  console.error(`\n${failures} route(s) fail the mobile rules — see docs/admin-mobile-design-system.md`);
  process.exit(1);
}
console.log('All routes clean.');
