type TranslatableEntity = {
  translations?: Record<string, Record<string, string>>;
};

export function localizedField(
  entity: TranslatableEntity,
  field: string,
  fallback: string,
  locale: string
): string {
  if (locale === "en") return fallback;
  return entity.translations?.[locale]?.[field] || fallback;
}
