'use client';

import { Input } from '@/components/ui';

interface FaqItem {
  question: string;
  answer: string;
}

interface FaqContent {
  header?: { title?: string; subtitle?: string };
  items?: FaqItem[];
  cta?: { title?: string; subtitle?: string; whatsapp_text?: string; contact_text?: string };
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

export function FaqEditor({ content, onChange }: Props) {
  const c = content as FaqContent;

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

  function setItem(idx: number, field: keyof FaqItem, value: string) {
    const items = [...(c.items ?? [])];
    items[idx] = { ...items[idx], [field]: value };
    onChange({ ...c, items } as Record<string, unknown>);
  }

  function addItem() {
    const items = [...(c.items ?? []), { question: '', answer: '' }];
    onChange({ ...c, items } as Record<string, unknown>);
  }

  function removeItem(idx: number) {
    const items = (c.items ?? []).filter((_, i) => i !== idx);
    onChange({ ...c, items } as Record<string, unknown>);
  }

  function moveItem(idx: number, dir: -1 | 1) {
    const items = [...(c.items ?? [])];
    const swap = idx + dir;
    if (swap < 0 || swap >= items.length) return;
    [items[idx], items[swap]] = [items[swap], items[idx]];
    onChange({ ...c, items } as Record<string, unknown>);
  }

  return (
    <div>
      <Section title="Header" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Title" value={c.header?.title ?? ''} onChange={e => set(['header', 'title'], e.target.value)} />
        <Input label="Subtitle" value={c.header?.subtitle ?? ''} onChange={e => set(['header', 'subtitle'], e.target.value)} />
      </div>

      <Section title="FAQ Items" />
      <div className="space-y-3">
        {(c.items ?? []).map((item, idx) => (
          <div key={idx} className="border border-gray-200 p-4 relative">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-body text-gray-400">Item {idx + 1}</span>
              <div className="flex items-center gap-1">
                <button onClick={() => moveItem(idx, -1)} className="text-gray-300 hover:text-primary transition-colors" disabled={idx === 0}>
                  <span className="material-icons text-[16px]">arrow_upward</span>
                </button>
                <button onClick={() => moveItem(idx, 1)} className="text-gray-300 hover:text-primary transition-colors" disabled={idx === (c.items?.length ?? 0) - 1}>
                  <span className="material-icons text-[16px]">arrow_downward</span>
                </button>
                <button onClick={() => removeItem(idx)} className="text-gray-300 hover:text-red-500 transition-colors ml-1">
                  <span className="material-icons text-[16px]">close</span>
                </button>
              </div>
            </div>
            <Input label="Question" value={item.question} onChange={e => setItem(idx, 'question', e.target.value)} />
            <div className="mt-3">
              <label className="block text-xs font-body uppercase tracking-widest text-gray-500 mb-1">Answer</label>
              <textarea
                rows={3}
                value={item.answer}
                onChange={e => setItem(idx, 'answer', e.target.value)}
                className="w-full text-sm font-body border border-gray-300 px-3 py-2 outline-none focus:border-primary resize-y"
              />
            </div>
          </div>
        ))}
        <button
          onClick={addItem}
          className="flex items-center gap-1.5 text-xs font-body text-primary hover:opacity-80 transition-opacity"
        >
          <span className="material-icons text-[14px]">add</span>
          Add FAQ Item
        </button>
      </div>

      <Section title="CTA" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Title" value={c.cta?.title ?? ''} onChange={e => set(['cta', 'title'], e.target.value)} />
        <Input label="Subtitle" value={c.cta?.subtitle ?? ''} onChange={e => set(['cta', 'subtitle'], e.target.value)} />
        <Input label="WhatsApp Button Text" value={c.cta?.whatsapp_text ?? ''} onChange={e => set(['cta', 'whatsapp_text'], e.target.value)} />
        <Input label="Contact Button Text" value={c.cta?.contact_text ?? ''} onChange={e => set(['cta', 'contact_text'], e.target.value)} />
      </div>

      <Section title="SEO" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Meta Title" value={c.seo?.title ?? ''} onChange={e => set(['seo', 'title'], e.target.value)} />
        <Input label="Meta Description" value={c.seo?.description ?? ''} onChange={e => set(['seo', 'description'], e.target.value)} />
      </div>
    </div>
  );
}
