import type { Language } from '@/lib/types';
import { Input, Textarea } from '@/components/ui';

interface TranslationField {
  key: string;
  label: string;
  type?: 'input' | 'textarea';
}

interface Props {
  languages: Language[];
  fields: TranslationField[];
  translations: Record<string, Record<string, string>>;
  onChange: (translations: Record<string, Record<string, string>>) => void;
}

export function TranslationFields({ languages, fields, translations, onChange }: Props) {
  if (languages.length === 0) return null;

  function handleChange(langCode: string, fieldKey: string, value: string) {
    const updated = { ...translations };
    if (!updated[langCode]) updated[langCode] = {};
    updated[langCode] = { ...updated[langCode], [fieldKey]: value };
    onChange(updated);
  }

  return (
    <div className="mt-4 space-y-4">
      <h3 className="text-xs font-body uppercase tracking-widest text-gray-500">Translations</h3>
      {languages.map(lang => (
        <div key={lang.code}>
          <p className="text-xs font-body font-medium text-gray-600 mb-2">{lang.native_name}</p>
          <div className="grid sm:grid-cols-2 gap-3">
            {fields.map(field => {
              const value = translations[lang.code]?.[field.key] ?? '';
              const dir = lang.direction === 'rtl' ? 'rtl' : undefined;
              return field.type === 'textarea' ? (
                <Textarea
                  key={field.key}
                  label={field.label}
                  value={value}
                  onChange={e => handleChange(lang.code, field.key, e.target.value)}
                  rows={2}
                  placeholder="Optional"
                  dir={dir}
                />
              ) : (
                <Input
                  key={field.key}
                  label={field.label}
                  value={value}
                  onChange={e => handleChange(lang.code, field.key, e.target.value)}
                  placeholder="Optional"
                  dir={dir}
                />
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
