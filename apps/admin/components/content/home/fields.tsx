'use client';

import { useId, useRef, useState, type ReactNode } from 'react';
import { uploadsApi, ApiError } from '@/lib/api';

/**
 * Relative image paths in the CMS (`/images/banners/...`) are served by the
 * storefront, not by the admin app, so previews have to be resolved against it.
 */
const STOREFRONT =
  process.env.NEXT_PUBLIC_STOREFRONT_URL ?? 'https://meltingmomentscakes.com';

/** Artwork that ships with the storefront repo, offered as autocomplete. */
export const BUNDLED_BANNERS = [
  '/images/banners/hero-cookie-melt.jpg',
  '/images/banners/hero-cookie-melt-mobile.jpg',
  '/images/banners/hero-mix-box.jpg',
  '/images/banners/hero-mix-box-mobile.jpg',
  '/images/banners/hero-brownies.jpg',
  '/images/banners/hero-brownies-mobile.jpg',
  '/images/banners/banner-cookies.jpg',
  '/images/banners/banner-cookies-mobile.jpg',
  '/images/banners/banner-desserts.jpg',
  '/images/banners/banner-desserts-mobile.jpg',
];

export const BUNDLED_TILES = [
  '/images/tiles/tile-cookiemelt.jpg',
  '/images/tiles/tile-brownies.jpg',
  '/images/tiles/tile-cookies.jpg',
  '/images/tiles/tile-mixboxes.jpg',
  '/images/tiles/tile-cakes.jpg',
  '/images/tiles/tile-desserts.jpg',
  '/images/tiles/tile-eggless.jpg',
  '/images/tiles/tile-extras.jpg',
];

export const BUNDLED_PHOTOS = [
  '/images/photos/person_shot_1.jpg',
  '/images/photos/person_shot_2.png',
  '/images/photos/person_shot_3.png',
  '/images/photos/person_shot_4.jpg',
];

export function previewSrc(value: string): string {
  if (!value) return '';
  return value.startsWith('/') ? `${STOREFRONT}${value}` : value;
}

// ─── Layout scaffolding ───────────────────────────────────────────────────────

export function Section({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mt-8 first:mt-0 mb-3 pb-2 border-b border-gray-100">
      <p className="text-[11px] font-body uppercase tracking-widest text-secondary">{title}</p>
      {hint && <p className="text-[11px] text-gray-400 mt-1">{hint}</p>}
    </div>
  );
}

export function Field({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  const id = useId();
  return (
    <div className="w-full">
      <label htmlFor={id} className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-2 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none transition-colors placeholder:text-gray-400 focus:border-primary focus:ring-1 focus:ring-primary/30"
      />
    </div>
  );
}

export function TextArea({
  label,
  value,
  onChange,
  rows = 2,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="w-full">
      <label htmlFor={id} className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
        {label}
      </label>
      <textarea
        id={id}
        rows={rows}
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-2 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 resize-y"
      />
      {hint && <p className="mt-1 text-[11px] text-gray-400">{hint}</p>}
    </div>
  );
}

// ─── Image field ──────────────────────────────────────────────────────────────

/**
 * A path/URL box with a live thumbnail, an upload button and autocomplete over
 * the artwork that ships with the repo. Uploads go to R2 through the same
 * endpoint the product form uses.
 */
export function ImageField({
  label,
  value,
  onChange,
  suggestions = BUNDLED_BANNERS,
  folder = 'banners',
  aspect = 'aspect-[12/5]',
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  suggestions?: string[];
  folder?: string;
  aspect?: string;
}) {
  const id = useId();
  const listId = `${id}-list`;
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  // A path that resolves on the storefront but not from here (a local admin
  // pointed at production, an image not deployed yet) should read as "no
  // preview", not as a broken-image glyph next to a perfectly valid path.
  const [broken, setBroken] = useState('');

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;

    setUploading(true);
    setError('');
    try {
      const { url } = await uploadsApi.uploadImage(file, folder);
      onChange(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload failed. Check R2 configuration.');
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="w-full">
      <label htmlFor={id} className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
        {label}
      </label>

      <div className="flex gap-3">
        <div className={`relative w-28 shrink-0 ${aspect} bg-gray-50 border border-gray-200 overflow-hidden`}>
          {value && broken !== value ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={previewSrc(value)}
              alt=""
              onError={() => setBroken(value)}
              className="absolute inset-0 h-full w-full object-cover"
            />
          ) : (
            <span className="absolute inset-0 flex items-center justify-center text-gray-300">
              <span className="material-icons text-[18px]">image</span>
            </span>
          )}
        </div>

        <div className="flex-1 min-w-0">
          <input
            id={id}
            list={listId}
            value={value}
            placeholder="/images/banners/hero-cookie-melt.jpg"
            onChange={e => onChange(e.target.value)}
            className="w-full px-3 py-2 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none placeholder:text-gray-400 focus:border-primary focus:ring-1 focus:ring-primary/30"
          />
          <datalist id={listId}>
            {suggestions.map(s => (
              <option key={s} value={s} />
            ))}
          </datalist>

          <div className="flex items-center gap-3 mt-1.5">
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              className="inline-flex items-center gap-1 text-[11px] font-body text-primary hover:opacity-80 disabled:opacity-50"
            >
              <span className="material-icons text-[13px]">upload</span>
              {uploading ? 'Uploading…' : 'Upload'}
            </button>
            {value && (
              <button
                type="button"
                onClick={() => onChange('')}
                className="text-[11px] font-body text-gray-400 hover:text-red-500"
              >
                Clear
              </button>
            )}
          </div>

          {error && <p className="mt-1 text-[11px] text-red-500">{error}</p>}
        </div>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        className="hidden"
        onChange={handleUpload}
      />
    </div>
  );
}

// ─── Repeatable rows ──────────────────────────────────────────────────────────

export function Repeatable<T>({
  items,
  onChange,
  blank,
  addLabel,
  title,
  children,
  max,
}: {
  items: T[];
  onChange: (next: T[]) => void;
  blank: () => T;
  addLabel: string;
  /** Row heading, e.g. `(i) => `Slide ${i + 1}``. */
  title?: (index: number, item: T) => string;
  children: (item: T, set: (patch: Partial<T>) => void, index: number) => ReactNode;
  max?: number;
}) {
  function move(index: number, dir: -1 | 1) {
    const next = [...items];
    const target = index + dir;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  return (
    <div className="space-y-3">
      {items.map((item, i) => (
        <div key={i} className="border border-gray-200 bg-gray-50/40 p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[11px] font-body uppercase tracking-widest text-gray-400">
              {title ? title(i, item) : `#${i + 1}`}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => move(i, -1)}
                disabled={i === 0}
                title="Move up"
                className="text-gray-300 hover:text-primary disabled:opacity-30 disabled:hover:text-gray-300"
              >
                <span className="material-icons text-[16px]">arrow_upward</span>
              </button>
              <button
                type="button"
                onClick={() => move(i, 1)}
                disabled={i === items.length - 1}
                title="Move down"
                className="text-gray-300 hover:text-primary disabled:opacity-30 disabled:hover:text-gray-300"
              >
                <span className="material-icons text-[16px]">arrow_downward</span>
              </button>
              <button
                type="button"
                onClick={() => onChange(items.filter((_, idx) => idx !== i))}
                title="Remove"
                className="text-gray-300 hover:text-red-500 ms-1"
              >
                <span className="material-icons text-[16px]">close</span>
              </button>
            </div>
          </div>

          {children(
            item,
            patch => onChange(items.map((it, idx) => (idx === i ? { ...it, ...patch } : it))),
            i,
          )}
        </div>
      ))}

      {(max === undefined || items.length < max) && (
        <button
          type="button"
          onClick={() => onChange([...items, blank()])}
          className="flex items-center gap-1.5 text-xs font-body text-primary hover:opacity-80"
        >
          <span className="material-icons text-[14px]">add</span>
          {addLabel}
        </button>
      )}
    </div>
  );
}
