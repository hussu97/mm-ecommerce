'use client';

import { Input } from '@/components/ui';

interface Value {
  icon: string;
  title: string;
  description: string;
}

interface AboutContent {
  hero?: { title?: string; subtitle?: string };
  story_1?: { label?: string; title?: string; body?: string; image_url?: string };
  story_2?: { label?: string; title?: string; body?: string; image_url?: string };
  values?: Value[];
  cta?: { title?: string; subtitle?: string; button_text?: string; button_link?: string };
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

export function AboutEditor({ content, onChange }: Props) {
  const c = content as AboutContent;

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

  function setValueField(idx: number, field: keyof Value, value: string) {
    const values = [...(c.values ?? [])];
    values[idx] = { ...values[idx], [field]: value };
    onChange({ ...c, values } as Record<string, unknown>);
  }

  function addValue() {
    const values = [...(c.values ?? []), { icon: 'star', title: '', description: '' }];
    onChange({ ...c, values } as Record<string, unknown>);
  }

  function removeValue(idx: number) {
    const values = (c.values ?? []).filter((_, i) => i !== idx);
    onChange({ ...c, values } as Record<string, unknown>);
  }

  return (
    <div>
      <Section title="Hero" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Title" value={c.hero?.title ?? ''} onChange={e => set(['hero', 'title'], e.target.value)} />
        <Input label="Subtitle" value={c.hero?.subtitle ?? ''} onChange={e => set(['hero', 'subtitle'], e.target.value)} />
      </div>

      <Section title="Story Section 1" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Label" value={c.story_1?.label ?? ''} onChange={e => set(['story_1', 'label'], e.target.value)} />
        <Input label="Title" value={c.story_1?.title ?? ''} onChange={e => set(['story_1', 'title'], e.target.value)} />
        <Input label="Image URL" value={c.story_1?.image_url ?? ''} onChange={e => set(['story_1', 'image_url'], e.target.value)} />
      </div>
      <div className="mt-4">
        <label className="block text-xs font-body uppercase tracking-widest text-gray-500 mb-1">Body</label>
        <textarea
          rows={5}
          value={c.story_1?.body ?? ''}
          onChange={e => set(['story_1', 'body'], e.target.value)}
          className="w-full text-sm font-body border border-gray-300 px-3 py-2 outline-none focus:border-primary resize-y"
        />
      </div>

      <Section title="Story Section 2" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Label" value={c.story_2?.label ?? ''} onChange={e => set(['story_2', 'label'], e.target.value)} />
        <Input label="Title" value={c.story_2?.title ?? ''} onChange={e => set(['story_2', 'title'], e.target.value)} />
        <Input label="Image URL" value={c.story_2?.image_url ?? ''} onChange={e => set(['story_2', 'image_url'], e.target.value)} />
      </div>
      <div className="mt-4">
        <label className="block text-xs font-body uppercase tracking-widest text-gray-500 mb-1">Body</label>
        <textarea
          rows={5}
          value={c.story_2?.body ?? ''}
          onChange={e => set(['story_2', 'body'], e.target.value)}
          className="w-full text-sm font-body border border-gray-300 px-3 py-2 outline-none focus:border-primary resize-y"
        />
      </div>

      <Section title="Values" />
      <div className="space-y-3">
        {(c.values ?? []).map((v, idx) => (
          <div key={idx} className="border border-gray-200 p-4 grid sm:grid-cols-3 gap-3 relative">
            <Input label="Icon (material)" value={v.icon} onChange={e => setValueField(idx, 'icon', e.target.value)} />
            <Input label="Title" value={v.title} onChange={e => setValueField(idx, 'title', e.target.value)} />
            <Input label="Description" value={v.description} onChange={e => setValueField(idx, 'description', e.target.value)} />
            <button
              onClick={() => removeValue(idx)}
              className="absolute top-2 right-2 text-gray-300 hover:text-red-500 transition-colors"
            >
              <span className="material-icons text-[16px]">close</span>
            </button>
          </div>
        ))}
        <button
          onClick={addValue}
          className="flex items-center gap-1.5 text-xs font-body text-primary hover:opacity-80 transition-opacity"
        >
          <span className="material-icons text-[14px]">add</span>
          Add Value
        </button>
      </div>

      <Section title="CTA Banner" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Title" value={c.cta?.title ?? ''} onChange={e => set(['cta', 'title'], e.target.value)} />
        <Input label="Subtitle" value={c.cta?.subtitle ?? ''} onChange={e => set(['cta', 'subtitle'], e.target.value)} />
        <Input label="Button Text" value={c.cta?.button_text ?? ''} onChange={e => set(['cta', 'button_text'], e.target.value)} />
        <Input label="Button Link" value={c.cta?.button_link ?? ''} onChange={e => set(['cta', 'button_link'], e.target.value)} />
      </div>

      <Section title="SEO" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Meta Title" value={c.seo?.title ?? ''} onChange={e => set(['seo', 'title'], e.target.value)} />
        <Input label="Meta Description" value={c.seo?.description ?? ''} onChange={e => set(['seo', 'description'], e.target.value)} />
      </div>
    </div>
  );
}
