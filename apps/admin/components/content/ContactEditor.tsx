'use client';

import { Input } from '@/components/ui';

interface ContactContent {
  header?: { title?: string; subtitle?: string };
  info?: {
    phone?: string;
    whatsapp?: string;
    email?: string;
    location?: string;
    location_detail?: string;
    hours?: string;
    hours_detail?: string;
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

export function ContactEditor({ content, onChange }: Props) {
  const c = content as ContactContent;

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

  return (
    <div>
      <Section title="Header" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Title" value={c.header?.title ?? ''} onChange={e => set(['header', 'title'], e.target.value)} />
        <Input label="Subtitle" value={c.header?.subtitle ?? ''} onChange={e => set(['header', 'subtitle'], e.target.value)} />
      </div>

      <Section title="Contact Info" />
      <p className="text-[11px] font-body text-gray-400 mb-3">
        Phone, email, and WhatsApp are shared across all languages. Translate location/hours labels per locale.
      </p>
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Phone" value={c.info?.phone ?? ''} onChange={e => set(['info', 'phone'], e.target.value)} />
        <Input label="WhatsApp URL" value={c.info?.whatsapp ?? ''} onChange={e => set(['info', 'whatsapp'], e.target.value)} />
        <Input label="Email" value={c.info?.email ?? ''} onChange={e => set(['info', 'email'], e.target.value)} />
        <Input label="Location" value={c.info?.location ?? ''} onChange={e => set(['info', 'location'], e.target.value)} />
        <Input label="Location Detail" value={c.info?.location_detail ?? ''} onChange={e => set(['info', 'location_detail'], e.target.value)} />
        <Input label="Hours" value={c.info?.hours ?? ''} onChange={e => set(['info', 'hours'], e.target.value)} />
        <Input label="Hours Detail" value={c.info?.hours_detail ?? ''} onChange={e => set(['info', 'hours_detail'], e.target.value)} />
      </div>

      <Section title="SEO" />
      <div className="grid sm:grid-cols-2 gap-4">
        <Input label="Meta Title" value={c.seo?.title ?? ''} onChange={e => set(['seo', 'title'], e.target.value)} />
        <Input label="Meta Description" value={c.seo?.description ?? ''} onChange={e => set(['seo', 'description'], e.target.value)} />
      </div>
    </div>
  );
}
