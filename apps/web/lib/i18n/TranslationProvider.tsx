"use client";

import { createContext, useContext, useCallback, useEffect, type ReactNode } from "react";

interface TranslationContextValue {
  locale: string;
  direction: "ltr" | "rtl";
  translations: Record<string, string>;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const TranslationContext = createContext<TranslationContextValue>({
  locale: "en",
  direction: "ltr",
  translations: {},
  t: (key) => key,
});

export function TranslationProvider({
  locale,
  direction,
  translations,
  children,
}: {
  locale: string;
  direction: "ltr" | "rtl";
  translations: Record<string, string>;
  children: ReactNode;
}) {
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = direction;
  }, [locale, direction]);

  const t = useCallback(
    (key: string, params?: Record<string, string | number>): string => {
      let value = translations[key] ?? key;
      if (params) {
        for (const [k, v] of Object.entries(params)) {
          value = value.replace(`{${k}}`, String(v));
        }
      }
      return value;
    },
    [translations]
  );

  return (
    <TranslationContext.Provider value={{ locale, direction, translations, t }}>
      {children}
    </TranslationContext.Provider>
  );
}

export function useTranslation() {
  return useContext(TranslationContext);
}
