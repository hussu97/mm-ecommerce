'use client';

import {
  BUNDLED_BANNERS,
  BUNDLED_PHOTOS,
  BUNDLED_TILES,
  Field,
  ImageField,
  Repeatable,
  Section,
  TextArea,
} from './home/fields';

// ─── Shape ────────────────────────────────────────────────────────────────────

interface HeroSlide {
  image?: string;
  image_mobile?: string;
  eyebrow?: string;
  headline?: string;
  highlight?: string;
  body?: string;
  cta_text?: string;
  cta_href?: string;
  secondary_text?: string;
  secondary_href?: string;
}

interface UspItem {
  icon?: string;
  label?: string;
}

interface CategoryTile {
  slug?: string;
  label?: string;
  image?: string;
  href?: string;
  meta?: string;
}

interface PromoItem {
  image?: string;
  image_mobile?: string;
  eyebrow?: string;
  title?: string;
  body?: string;
  cta_text?: string;
  cta_href?: string;
}

interface GalleryItem {
  image?: string;
  alt?: string;
}

interface Occasion {
  icon: string;
  label: string;
}

/** Copy for the storefront's custom-order enquiry form (the "We cater to"
 *  section). Every field is optional; the storefront falls back to English. */
interface CaterForm {
  heading?: string;
  intro?: string;
  name_label?: string;
  phone_label?: string;
  description_label?: string;
  description_placeholder?: string;
  kg_label?: string;
  images_label?: string;
  images_hint?: string;
  delivery_label?: string;
  delivery_note?: string;
  submit_label?: string;
  success_title?: string;
  success_body?: string;
}

interface HomeContent {
  layout?: { order?: string[]; hidden?: string[] };
  hero?: { slides?: HeroSlide[]; autoplay_ms?: number };
  usps?: { items?: UspItem[]; speed_s?: number; theme?: string };
  featured?: {
    title?: string;
    subtitle?: string;
    view_all_text?: string;
    view_all_href?: string;
    badge_text?: string;
  };
  categories?: {
    title?: string;
    subtitle?: string;
    count_suffix?: string;
    tiles?: CategoryTile[];
  };
  promos?: { items?: PromoItem[] };
  baker?: {
    label?: string;
    quote?: string;
    body?: string;
    button_text?: string;
    button_href?: string;
    image?: string;
    gallery?: GalleryItem[];
  };
  cater?: {
    title?: string;
    subtitle?: string;
    cta_text?: string;
    cta_href?: string;
    occasions?: Occasion[];
    gallery?: GalleryItem[];
    form?: CaterForm;
  };
  seo?: { title?: string; description?: string };
}

/** Must stay in step with SECTION_ORDER in apps/web/app/[locale]/page.tsx. */
const SECTIONS: { key: string; label: string }[] = [
  { key: 'hero', label: 'Hero carousel' },
  { key: 'usps', label: 'Why-us strip' },
  { key: 'featured', label: 'Bestsellers' },
  { key: 'categories', label: 'Category tiles' },
  { key: 'promos', label: 'Promo bands' },
  { key: 'baker', label: 'Meet the baker' },
  { key: 'cater', label: 'We cater to' },
];

const THEME_OPTIONS = [
  { value: 'plum', label: 'Plum (brand)' },
  { value: 'cream', label: 'Cream' },
];

interface Props {
  content: Record<string, unknown>;
  onChange: (content: Record<string, unknown>) => void;
}

export function HomeEditor({ content, onChange }: Props) {
  const c = content as HomeContent;

  /** Replace one top-level block, leaving the rest of the document alone. */
  function setBlock<K extends keyof HomeContent>(key: K, patch: Partial<NonNullable<HomeContent[K]>>) {
    onChange({ ...c, [key]: { ...(c[key] as object | undefined), ...patch } } as Record<string, unknown>);
  }

  // ── Section order & visibility ──────────────────────────────────────────────

  const hidden = new Set(c.layout?.hidden ?? []);
  const configured = (c.layout?.order ?? []).filter(k => SECTIONS.some(s => s.key === k));
  const order = [...configured, ...SECTIONS.map(s => s.key).filter(k => !configured.includes(k))];

  function moveSection(index: number, dir: -1 | 1) {
    const next = [...order];
    const target = index + dir;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setBlock('layout', { order: next, hidden: [...hidden] });
  }

  function toggleSection(key: string) {
    const next = new Set(hidden);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setBlock('layout', { order, hidden: [...next] });
  }

  return (
    <div>
      {/* ── Layout ─────────────────────────────────────────────────────────── */}
      <Section
        title="Page layout"
        hint="Order and visibility of the home page sections, for the language you are editing."
      />
      <div className="space-y-1.5">
        {order.map((key, i) => {
          const section = SECTIONS.find(s => s.key === key)!;
          const isHidden = hidden.has(key);
          return (
            <div
              key={key}
              className={`flex items-center justify-between border px-3 py-2 ${
                isHidden ? 'border-gray-200 bg-gray-50' : 'border-gray-200 bg-white'
              }`}
            >
              <span
                className={`text-xs font-body ${isHidden ? 'text-gray-400 line-through' : 'text-gray-700'}`}
              >
                {i + 1}. {section.label}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => toggleSection(key)}
                  title={isHidden ? 'Show section' : 'Hide section'}
                  className="text-gray-300 hover:text-primary"
                >
                  <span className="material-icons text-[16px]">
                    {isHidden ? 'visibility_off' : 'visibility'}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => moveSection(i, -1)}
                  disabled={i === 0}
                  title="Move up"
                  className="text-gray-300 hover:text-primary disabled:opacity-30"
                >
                  <span className="material-icons text-[16px]">arrow_upward</span>
                </button>
                <button
                  type="button"
                  onClick={() => moveSection(i, 1)}
                  disabled={i === order.length - 1}
                  title="Move down"
                  className="text-gray-300 hover:text-primary disabled:opacity-30"
                >
                  <span className="material-icons text-[16px]">arrow_downward</span>
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Hero ───────────────────────────────────────────────────────────── */}
      <Section
        title="Hero carousel"
        hint="Slides rotate automatically. Wide art shows from tablet up; the mobile crop is used on phones."
      />
      <div className="mb-4 max-w-xs">
        <Field
          label="Seconds per slide"
          type="number"
          value={String((c.hero?.autoplay_ms ?? 6000) / 1000)}
          onChange={v => setBlock('hero', { autoplay_ms: Math.max(0, Number(v) || 0) * 1000 })}
        />
        <p className="mt-1 text-[11px] text-gray-400">0 stops the carousel on the first slide.</p>
      </div>

      <Repeatable<HeroSlide>
        items={c.hero?.slides ?? []}
        onChange={slides => setBlock('hero', { slides })}
        blank={() => ({ cta_href: '/all-products' })}
        addLabel="Add slide"
        title={i => `Slide ${i + 1}`}
      >
        {(slide, set) => (
          <div className="space-y-4">
            <div className="grid sm:grid-cols-2 gap-4">
              <ImageField
                label="Wide image (desktop)"
                value={slide.image ?? ''}
                onChange={image => set({ image })}
                suggestions={BUNDLED_BANNERS}
              />
              <ImageField
                label="Tall image (mobile)"
                value={slide.image_mobile ?? ''}
                onChange={image_mobile => set({ image_mobile })}
                suggestions={BUNDLED_BANNERS}
                aspect="aspect-[4/5]"
              />
            </div>
            <div className="grid sm:grid-cols-3 gap-4">
              <Field label="Eyebrow" value={slide.eyebrow ?? ''} onChange={eyebrow => set({ eyebrow })} />
              <Field label="Headline" value={slide.headline ?? ''} onChange={headline => set({ headline })} />
              <Field
                label="Highlight line"
                value={slide.highlight ?? ''}
                onChange={highlight => set({ highlight })}
                placeholder="Shown in brand plum"
              />
            </div>
            <TextArea
              label="Body"
              value={slide.body ?? ''}
              onChange={body => set({ body })}
              hint="Optional, and hidden on phones. Keep the hero visual — one line at most."
            />
            <div className="grid sm:grid-cols-4 gap-4">
              <Field label="Button text" value={slide.cta_text ?? ''} onChange={cta_text => set({ cta_text })} />
              <Field label="Button link" value={slide.cta_href ?? ''} onChange={cta_href => set({ cta_href })} />
              <Field
                label="Second button"
                value={slide.secondary_text ?? ''}
                onChange={secondary_text => set({ secondary_text })}
              />
              <Field
                label="Second link"
                value={slide.secondary_href ?? ''}
                onChange={secondary_href => set({ secondary_href })}
              />
            </div>
          </div>
        )}
      </Repeatable>

      {/* ── USP marquee ────────────────────────────────────────────────────── */}
      <Section
        title="Why-us strip"
        hint="The scrolling band under the hero. Icons are Material Icons names (local_shipping) or an emoji."
      />
      <div className="grid sm:grid-cols-2 gap-4 mb-4 max-w-md">
        <div>
          <label className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
            Colour
          </label>
          <select
            value={c.usps?.theme ?? 'plum'}
            onChange={e => setBlock('usps', { theme: e.target.value })}
            className="w-full px-3 py-2 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none cursor-pointer focus:border-primary focus:ring-1 focus:ring-primary/30"
          >
            {THEME_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <Field
          label="Seconds per loop"
          type="number"
          value={String(c.usps?.speed_s ?? 38)}
          onChange={v => setBlock('usps', { speed_s: Math.max(5, Number(v) || 38) })}
        />
      </div>

      <Repeatable<UspItem>
        items={c.usps?.items ?? []}
        onChange={items => setBlock('usps', { items })}
        blank={() => ({ icon: 'favorite', label: '' })}
        addLabel="Add reason"
      >
        {(item, set) => (
          <div className="grid grid-cols-[140px_1fr] gap-3">
            <Field label="Icon" value={item.icon ?? ''} onChange={icon => set({ icon })} />
            <Field label="Label" value={item.label ?? ''} onChange={label => set({ label })} />
          </div>
        )}
      </Repeatable>

      {/* ── Bestsellers ────────────────────────────────────────────────────── */}
      <Section title="Bestsellers" hint="Products come from the Featured flag on each product." />
      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Title" value={c.featured?.title ?? ''} onChange={title => setBlock('featured', { title })} />
        <Field
          label="Subtitle"
          value={c.featured?.subtitle ?? ''}
          onChange={subtitle => setBlock('featured', { subtitle })}
        />
        <Field
          label="View-all text"
          value={c.featured?.view_all_text ?? ''}
          onChange={view_all_text => setBlock('featured', { view_all_text })}
        />
        <Field
          label="View-all link"
          value={c.featured?.view_all_href ?? ''}
          onChange={view_all_href => setBlock('featured', { view_all_href })}
        />
        <Field
          label="Card badge"
          value={c.featured?.badge_text ?? ''}
          onChange={badge_text => setBlock('featured', { badge_text })}
          placeholder="Leave blank for no badge"
        />
      </div>

      {/* ── Category tiles ─────────────────────────────────────────────────── */}
      <Section
        title="Category tiles"
        hint="Leave the list empty to show every active category automatically. The first tile takes a double-width block."
      />
      <div className="grid sm:grid-cols-3 gap-4 mb-4">
        <Field
          label="Title"
          value={c.categories?.title ?? ''}
          onChange={title => setBlock('categories', { title })}
        />
        <Field
          label="Subtitle"
          value={c.categories?.subtitle ?? ''}
          onChange={subtitle => setBlock('categories', { subtitle })}
        />
        <Field
          label="Count noun"
          value={c.categories?.count_suffix ?? ''}
          onChange={count_suffix => setBlock('categories', { count_suffix })}
          placeholder="treats — blank hides counts"
        />
      </div>

      <Repeatable<CategoryTile>
        items={c.categories?.tiles ?? []}
        onChange={tiles => setBlock('categories', { tiles })}
        blank={() => ({ slug: '' })}
        addLabel="Add tile"
        title={(i, tile) => tile.label || tile.slug || `Tile ${i + 1}`}
      >
        {(tile, set) => (
          <div className="space-y-4">
            <div className="grid sm:grid-cols-3 gap-4">
              <Field
                label="Category slug"
                value={tile.slug ?? ''}
                onChange={slug => set({ slug })}
                placeholder="brownies"
              />
              <Field
                label="Label override"
                value={tile.label ?? ''}
                onChange={label => set({ label })}
                placeholder="Defaults to the category name"
              />
              <Field
                label="Link override"
                value={tile.href ?? ''}
                onChange={href => set({ href })}
                placeholder="Defaults to the category page"
              />
            </div>
            <div className="grid sm:grid-cols-2 gap-4">
              <ImageField
                label="Tile image"
                value={tile.image ?? ''}
                onChange={image => set({ image })}
                suggestions={BUNDLED_TILES}
                folder="tiles"
                aspect="aspect-square"
              />
              <Field
                label="Caption override"
                value={tile.meta ?? ''}
                onChange={meta => set({ meta })}
                placeholder="Defaults to the live product count"
              />
            </div>
          </div>
        )}
      </Repeatable>

      {/* ── Promo bands ────────────────────────────────────────────────────── */}
      <Section title="Promo bands" hint="Full-width photo bands. One photo, a few words, one link." />
      <Repeatable<PromoItem>
        items={c.promos?.items ?? []}
        onChange={items => setBlock('promos', { items })}
        blank={() => ({ cta_href: '/all-products' })}
        addLabel="Add band"
        title={(i, promo) => promo.title || `Band ${i + 1}`}
      >
        {(promo, set) => (
          <div className="space-y-4">
            <div className="grid sm:grid-cols-2 gap-4">
              <ImageField
                label="Wide image (desktop)"
                value={promo.image ?? ''}
                onChange={image => set({ image })}
                suggestions={BUNDLED_BANNERS}
              />
              <ImageField
                label="Tall image (mobile)"
                value={promo.image_mobile ?? ''}
                onChange={image_mobile => set({ image_mobile })}
                suggestions={BUNDLED_BANNERS}
                aspect="aspect-[4/5]"
              />
            </div>
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="Eyebrow" value={promo.eyebrow ?? ''} onChange={eyebrow => set({ eyebrow })} />
              <Field label="Title" value={promo.title ?? ''} onChange={title => set({ title })} />
              <Field label="Button text" value={promo.cta_text ?? ''} onChange={cta_text => set({ cta_text })} />
              <Field label="Button link" value={promo.cta_href ?? ''} onChange={cta_href => set({ cta_href })} />
            </div>
            <TextArea
              label="Body"
              value={promo.body ?? ''}
              onChange={body => set({ body })}
              hint="Optional, and hidden on phones."
            />
          </div>
        )}
      </Repeatable>

      {/* ── Meet the baker ─────────────────────────────────────────────────── */}
      <Section title="Meet the baker" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Label" value={c.baker?.label ?? ''} onChange={label => setBlock('baker', { label })} />
        <Field label="Quote" value={c.baker?.quote ?? ''} onChange={quote => setBlock('baker', { quote })} />
        <Field
          label="Button text"
          value={c.baker?.button_text ?? ''}
          onChange={button_text => setBlock('baker', { button_text })}
        />
        <Field
          label="Button link"
          value={c.baker?.button_href ?? ''}
          onChange={button_href => setBlock('baker', { button_href })}
        />
      </div>
      <div className="mt-4 grid sm:grid-cols-2 gap-4">
        <TextArea
          label="Body"
          value={c.baker?.body ?? ''}
          onChange={body => setBlock('baker', { body })}
          hint="One line. The full story lives on the About page."
        />
        <ImageField
          label="Backdrop photo"
          value={c.baker?.image ?? ''}
          onChange={image => setBlock('baker', { image })}
          suggestions={BUNDLED_PHOTOS}
          folder="photos"
          aspect="aspect-[4/3]"
        />
      </div>

      <div className="mt-4">
        <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">Kitchen strip</p>
        <Repeatable<GalleryItem>
          items={c.baker?.gallery ?? []}
          onChange={gallery => setBlock('baker', { gallery })}
          blank={() => ({})}
          addLabel="Add photo"
          max={3}
        >
          {(shot, set) => (
            <div className="grid sm:grid-cols-2 gap-4">
              <ImageField
                label="Photo"
                value={shot.image ?? ''}
                onChange={image => set({ image })}
                suggestions={BUNDLED_PHOTOS}
                folder="photos"
                aspect="aspect-square"
              />
              <Field label="Alt text" value={shot.alt ?? ''} onChange={alt => set({ alt })} />
            </div>
          )}
        </Repeatable>
      </div>

      {/* ── We cater to ────────────────────────────────────────────────────── */}
      <Section title="We cater to" />
      <div className="grid sm:grid-cols-2 gap-4 mb-4">
        <Field label="Title" value={c.cater?.title ?? ''} onChange={title => setBlock('cater', { title })} />
        <Field
          label="Subtitle"
          value={c.cater?.subtitle ?? ''}
          onChange={subtitle => setBlock('cater', { subtitle })}
        />
        <Field
          label="Button text"
          value={c.cater?.cta_text ?? ''}
          onChange={cta_text => setBlock('cater', { cta_text })}
        />
        <Field
          label="Button link"
          value={c.cater?.cta_href ?? ''}
          onChange={cta_href => setBlock('cater', { cta_href })}
        />
      </div>

      <Repeatable<Occasion>
        items={c.cater?.occasions ?? []}
        onChange={occasions => setBlock('cater', { occasions })}
        blank={() => ({ icon: '🎉', label: '' })}
        addLabel="Add occasion"
      >
        {(occasion, set) => (
          <div className="grid grid-cols-[100px_1fr] gap-3">
            <Field label="Emoji" value={occasion.icon} onChange={icon => set({ icon })} />
            <Field label="Label" value={occasion.label} onChange={label => set({ label })} />
          </div>
        )}
      </Repeatable>

      <div className="mt-4">
        <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">
          Custom-cake gallery
        </p>
        <Repeatable<GalleryItem>
          items={c.cater?.gallery ?? []}
          onChange={gallery => setBlock('cater', { gallery })}
          blank={() => ({})}
          addLabel="Add cake photo"
          max={24}
        >
          {(shot, set) => (
            <div className="grid sm:grid-cols-2 gap-4">
              <ImageField
                label="Photo"
                value={shot.image ?? ''}
                onChange={image => set({ image })}
                suggestions={BUNDLED_PHOTOS}
                folder="cater"
                aspect="aspect-[3/4]"
              />
              <Field
                label="Caption / occasion"
                value={shot.alt ?? ''}
                onChange={alt => set({ alt })}
              />
            </div>
          )}
        </Repeatable>
      </div>

      <div className="mt-4">
        <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">
          Enquiry form
        </p>
        <div className="grid sm:grid-cols-2 gap-4">
          <Field label="Form heading" value={c.cater?.form?.heading ?? ''} onChange={heading => setBlock('cater', { form: { ...c.cater?.form, heading } })} />
          <Field label="Submit button" value={c.cater?.form?.submit_label ?? ''} onChange={submit_label => setBlock('cater', { form: { ...c.cater?.form, submit_label } })} />
        </div>
        <div className="mt-4">
          <TextArea label="Intro" value={c.cater?.form?.intro ?? ''} onChange={intro => setBlock('cater', { form: { ...c.cater?.form, intro } })} />
        </div>
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Field label="Name label" value={c.cater?.form?.name_label ?? ''} onChange={name_label => setBlock('cater', { form: { ...c.cater?.form, name_label } })} />
          <Field label="Phone label" value={c.cater?.form?.phone_label ?? ''} onChange={phone_label => setBlock('cater', { form: { ...c.cater?.form, phone_label } })} />
          <Field label="Description label" value={c.cater?.form?.description_label ?? ''} onChange={description_label => setBlock('cater', { form: { ...c.cater?.form, description_label } })} />
          <Field label="Description placeholder" value={c.cater?.form?.description_placeholder ?? ''} onChange={description_placeholder => setBlock('cater', { form: { ...c.cater?.form, description_placeholder } })} />
          <Field label="Weight (kg) label" value={c.cater?.form?.kg_label ?? ''} onChange={kg_label => setBlock('cater', { form: { ...c.cater?.form, kg_label } })} />
          <Field label="Photos label" value={c.cater?.form?.images_label ?? ''} onChange={images_label => setBlock('cater', { form: { ...c.cater?.form, images_label } })} />
          <Field label="Photos hint" value={c.cater?.form?.images_hint ?? ''} onChange={images_hint => setBlock('cater', { form: { ...c.cater?.form, images_hint } })} />
          <Field label="Delivery-by label" value={c.cater?.form?.delivery_label ?? ''} onChange={delivery_label => setBlock('cater', { form: { ...c.cater?.form, delivery_label } })} />
        </div>
        <div className="mt-4">
          <TextArea label="Delivery-by note" value={c.cater?.form?.delivery_note ?? ''} onChange={delivery_note => setBlock('cater', { form: { ...c.cater?.form, delivery_note } })} />
        </div>
        <div className="grid sm:grid-cols-2 gap-4 mt-4">
          <Field label="Success title" value={c.cater?.form?.success_title ?? ''} onChange={success_title => setBlock('cater', { form: { ...c.cater?.form, success_title } })} />
        </div>
        <div className="mt-4">
          <TextArea label="Success message" value={c.cater?.form?.success_body ?? ''} onChange={success_body => setBlock('cater', { form: { ...c.cater?.form, success_body } })} />
        </div>
      </div>

      {/* ── SEO ────────────────────────────────────────────────────────────── */}
      <Section title="SEO" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Field label="Meta title" value={c.seo?.title ?? ''} onChange={title => setBlock('seo', { title })} />
        <Field
          label="Meta description"
          value={c.seo?.description ?? ''}
          onChange={description => setBlock('seo', { description })}
        />
      </div>
    </div>
  );
}
