'use client';

import { Input } from '@/components/ui';

interface Occasion {
  icon: string;
  label: string;
}

interface HomeContent {
  hero?: {
    tagline?: string;
    headline?: string;
    body?: string;
    shop_button_text?: string;
    shop_button_href?: string;
    story_button_text?: string;
    story_button_href?: string;
  };
  featured?: {
    title?: string;
    view_all_text?: string;
    view_all_href?: string;
  };
  baker?: {
    label?: string;
    quote?: string;
    body?: string;
    button_text?: string;
    button_href?: string;
  };
  cater?: {
    title?: string;
    occasions?: Occasion[];
  };
  seo?: { title?: string; description?: string };
}

interface Props {
  content: Record<string, unknown>;
  onChange: (content: Record<string, unknown>) => void;
}

function Section({ title }: { title: string }) {
  return (
    <p className="text-[11px] font-body uppercase tracking-widest text-secondary mb-3 mt-6 first:mt-0">
      {title}
    </p>
  );
}

export function HomeEditor({ content, onChange }: Props) {
  const c = content as HomeContent;

  function set(path: string[], value: string) {
    const updated = structuredClone(c) as Record<string, unknown>;
    let cur: Record<string, unknown> = updated;
    for (let i = 0; i < path.length - 1; i++) {
      if (!cur[path[i]]) cur[path[i]] = {};
      cur = cur[path[i]] as Record<string, unknown>;
    }
    cur[path[path.length - 1]] = value;
    onChange(updated);
  }

  function setOccasionField(idx: number, field: keyof Occasion, value: string) {
    const occasions = [...(c.cater?.occasions ?? [])];
    occasions[idx] = { ...occasions[idx], [field]: value };
    onChange({ ...c, cater: { ...c.cater, occasions } } as Record<string, unknown>);
  }

  function addOccasion() {
    const occasions = [...(c.cater?.occasions ?? []), { icon: '🎉', label: '' }];
    onChange({ ...c, cater: { ...c.cater, occasions } } as Record<string, unknown>);
  }

  function removeOccasion(idx: number) {
    const occasions = (c.cater?.occasions ?? []).filter((_, i) => i !== idx);
    onChange({ ...c, cater: { ...c.cater, occasions } } as Record<string, unknown>);
  }

  return (
    <div>
      <Section title="Hero" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Tagline" value={c.hero?.tagline ?? ''} onChange={e => set(['hero', 'tagline'], e.target.value)} />
        <Input label="Headline" value={c.hero?.headline ?? ''} onChange={e => set(['hero', 'headline'], e.target.value)} />
        <Input label="Shop Button Text" value={c.hero?.shop_button_text ?? ''} onChange={e => set(['hero', 'shop_button_text'], e.target.value)} />
        <Input label="Shop Button Link" value={c.hero?.shop_button_href ?? ''} onChange={e => set(['hero', 'shop_button_href'], e.target.value)} />
        <Input label="Story Button Text" value={c.hero?.story_button_text ?? ''} onChange={e => set(['hero', 'story_button_text'], e.target.value)} />
        <Input label="Story Button Link" value={c.hero?.story_button_href ?? ''} onChange={e => set(['hero', 'story_button_href'], e.target.value)} />
      </div>
      <div className="mt-4">
        <label className="block text-xs font-body uppercase tracking-widest text-gray-500 mb-1">Body</label>
        <textarea
          rows={3}
          value={c.hero?.body ?? ''}
          onChange={e => set(['hero', 'body'], e.target.value)}
          className="w-full text-sm font-body border border-gray-300 px-3 py-2 outline-none focus:border-primary resize-y"
        />
      </div>

      <Section title="Featured Products" />
      <div className="grid sm:grid-cols-3 gap-4">
        <Input label="Section Title" value={c.featured?.title ?? ''} onChange={e => set(['featured', 'title'], e.target.value)} />
        <Input label="View All Text" value={c.featured?.view_all_text ?? ''} onChange={e => set(['featured', 'view_all_text'], e.target.value)} />
        <Input label="View All Link" value={c.featured?.view_all_href ?? ''} onChange={e => set(['featured', 'view_all_href'], e.target.value)} />
      </div>

      <Section title="Meet the Baker" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Label" value={c.baker?.label ?? ''} onChange={e => set(['baker', 'label'], e.target.value)} />
        <Input label="Quote" value={c.baker?.quote ?? ''} onChange={e => set(['baker', 'quote'], e.target.value)} />
        <Input label="Button Text" value={c.baker?.button_text ?? ''} onChange={e => set(['baker', 'button_text'], e.target.value)} />
        <Input label="Button Link" value={c.baker?.button_href ?? ''} onChange={e => set(['baker', 'button_href'], e.target.value)} />
      </div>
      <div className="mt-4">
        <label className="block text-xs font-body uppercase tracking-widest text-gray-500 mb-1">Body</label>
        <textarea
          rows={3}
          value={c.baker?.body ?? ''}
          onChange={e => set(['baker', 'body'], e.target.value)}
          className="w-full text-sm font-body border border-gray-300 px-3 py-2 outline-none focus:border-primary resize-y"
        />
      </div>

      <Section title="We Cater To" />
      <div className="mb-3">
        <Input label="Section Title" value={c.cater?.title ?? ''} onChange={e => set(['cater', 'title'], e.target.value)} />
      </div>
      <div className="space-y-2">
        {(c.cater?.occasions ?? []).map((occ, idx) => (
          <div key={idx} className="border border-gray-200 p-3 grid grid-cols-[80px_1fr_auto] gap-3 items-end">
            <Input label="Icon (emoji)" value={occ.icon} onChange={e => setOccasionField(idx, 'icon', e.target.value)} />
            <Input label="Label" value={occ.label} onChange={e => setOccasionField(idx, 'label', e.target.value)} />
            <button
              onClick={() => removeOccasion(idx)}
              className="mb-[1px] text-gray-300 hover:text-red-500 transition-colors"
            >
              <span className="material-icons text-[16px]">close</span>
            </button>
          </div>
        ))}
        <button
          onClick={addOccasion}
          className="flex items-center gap-1.5 text-xs font-body text-primary hover:opacity-80 transition-opacity"
        >
          <span className="material-icons text-[14px]">add</span>
          Add Occasion
        </button>
      </div>

      <Section title="SEO" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Meta Title" value={c.seo?.title ?? ''} onChange={e => set(['seo', 'title'], e.target.value)} />
        <Input label="Meta Description" value={c.seo?.description ?? ''} onChange={e => set(['seo', 'description'], e.target.value)} />
      </div>
    </div>
  );
}
