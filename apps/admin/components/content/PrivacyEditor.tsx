'use client';

import { Input } from '@/components/ui';

interface Section {
  title: string;
  body: string;
}

interface PrivacyContent {
  header?: { title?: string; subtitle?: string };
  intro?: string;
  sections?: Section[];
  contact?: { email?: string; phone?: string };
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

export function PrivacyEditor({ content, onChange }: Props) {
  const c = content as PrivacyContent;

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

  function setSectionField(idx: number, field: keyof Section, value: string) {
    const sections = [...(c.sections ?? [])];
    sections[idx] = { ...sections[idx], [field]: value };
    onChange({ ...c, sections } as Record<string, unknown>);
  }

  function addSection() {
    const sections = [...(c.sections ?? []), { title: '', body: '' }];
    onChange({ ...c, sections } as Record<string, unknown>);
  }

  function removeSection(idx: number) {
    const sections = (c.sections ?? []).filter((_, i) => i !== idx);
    onChange({ ...c, sections } as Record<string, unknown>);
  }

  function moveSection(idx: number, dir: -1 | 1) {
    const sections = [...(c.sections ?? [])];
    const swap = idx + dir;
    if (swap < 0 || swap >= sections.length) return;
    [sections[idx], sections[swap]] = [sections[swap], sections[idx]];
    onChange({ ...c, sections } as Record<string, unknown>);
  }

  return (
    <div>
      <Section title="Header" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Title" value={c.header?.title ?? ''} onChange={e => set(['header', 'title'], e.target.value)} />
        <Input label="Subtitle (e.g. last updated)" value={c.header?.subtitle ?? ''} onChange={e => set(['header', 'subtitle'], e.target.value)} />
      </div>

      <Section title="Introduction" />
      <textarea
        rows={4}
        value={c.intro ?? ''}
        onChange={e => set(['intro'], e.target.value)}
        placeholder="Opening paragraph(s)…"
        className="w-full text-sm font-body border border-gray-300 px-3 py-2 outline-none focus:border-primary resize-y"
      />

      <Section title="Sections" />
      <div className="space-y-3">
        {(c.sections ?? []).map((section, idx) => (
          <div key={idx} className="border border-gray-200 p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-body text-gray-400">Section {idx + 1}</span>
              <div className="flex items-center gap-1">
                <button onClick={() => moveSection(idx, -1)} disabled={idx === 0} className="text-gray-300 hover:text-primary disabled:opacity-30 transition-colors">
                  <span className="material-icons text-[16px]">arrow_upward</span>
                </button>
                <button onClick={() => moveSection(idx, 1)} disabled={idx === (c.sections?.length ?? 0) - 1} className="text-gray-300 hover:text-primary disabled:opacity-30 transition-colors">
                  <span className="material-icons text-[16px]">arrow_downward</span>
                </button>
                <button onClick={() => removeSection(idx)} className="text-gray-300 hover:text-red-500 transition-colors ml-1">
                  <span className="material-icons text-[16px]">close</span>
                </button>
              </div>
            </div>
            <Input
              label="Section Title"
              value={section.title}
              onChange={e => setSectionField(idx, 'title', e.target.value)}
            />
            <div className="mt-3">
              <label className="block text-xs font-body uppercase tracking-widest text-gray-500 mb-1">Body</label>
              <textarea
                rows={5}
                value={section.body}
                onChange={e => setSectionField(idx, 'body', e.target.value)}
                className="w-full text-sm font-body border border-gray-300 px-3 py-2 outline-none focus:border-primary resize-y"
              />
            </div>
          </div>
        ))}
        <button
          onClick={addSection}
          className="flex items-center gap-1.5 text-xs font-body text-primary hover:opacity-80 transition-opacity"
        >
          <span className="material-icons text-[14px]">add</span>
          Add Section
        </button>
      </div>

      <Section title="Contact" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Email" value={c.contact?.email ?? ''} onChange={e => set(['contact', 'email'], e.target.value)} />
        <Input label="Phone" value={c.contact?.phone ?? ''} onChange={e => set(['contact', 'phone'], e.target.value)} />
      </div>

      <Section title="SEO" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Meta Title" value={c.seo?.title ?? ''} onChange={e => set(['seo', 'title'], e.target.value)} />
        <Input label="Meta Description" value={c.seo?.description ?? ''} onChange={e => set(['seo', 'description'], e.target.value)} />
      </div>
    </div>
  );
}
