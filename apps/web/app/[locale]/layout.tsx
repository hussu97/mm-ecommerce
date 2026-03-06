import { notFound } from "next/navigation";
import { TranslationProvider } from "@/lib/i18n/TranslationProvider";
import { getTranslations, getLanguages } from "@/lib/i18n/server";
import { Header } from "@/components/layout/Header";
import { CategoryNavLinks } from "@/components/layout/CategoryNav";
import { Footer } from "@/components/layout/Footer";
import { PromoBanner } from "@/components/layout/PromoBanner";
import type { Category, Language } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const SUPPORTED_LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? "en,ar").split(",");

export function generateStaticParams() {
  return SUPPORTED_LOCALES.map((locale) => ({ locale }));
}

async function getActiveCategories(): Promise<Category[]> {
  try {
    const res = await fetch(`${API_BASE}/categories`, { next: { revalidate: 300 } });
    if (!res.ok) return [];
    const data: Category[] = await res.json();
    return data.filter((c) => c.is_active).sort((a, b) => a.display_order - b.display_order);
  } catch {
    return [];
  }
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;

  if (!SUPPORTED_LOCALES.includes(locale)) {
    notFound();
  }

  const [categories, translations, languages] = await Promise.all([
    getActiveCategories(),
    getTranslations(locale),
    getLanguages(),
  ]);

  const currentLang: Language | undefined = languages.find((l) => l.code === locale);
  const direction = (currentLang?.direction ?? "ltr") as "ltr" | "rtl";

  return (
    <TranslationProvider locale={locale} direction={direction} translations={translations}>
      <PromoBanner />
      <Header languages={languages} categories={categories} locale={locale} />
      <CategoryNavLinks categories={categories} locale={locale} />
      <main className="flex-1">
        {children}
      </main>
      <Footer />
    </TranslationProvider>
  );
}
