import type { Language } from "@/lib/types";
import { API_BASE } from "@/lib/api";

export async function getTranslations(locale: string): Promise<Record<string, string>> {
  try {
    const res = await fetch(`${API_BASE}/i18n/translations/${locale}`, {
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return {};
    return await res.json();
  } catch {
    return {};
  }
}

export async function getLanguages(): Promise<Language[]> {
  try {
    const res = await fetch(`${API_BASE}/i18n/languages`, {
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export function createT(translations: Record<string, string>) {
  return function t(key: string, params?: Record<string, string | number>): string {
    let value = translations[key] ?? key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        value = value.replace(`{${k}}`, String(v));
      }
    }
    return value;
  };
}
