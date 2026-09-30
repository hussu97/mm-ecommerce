import type { EmailTemplateOption } from './api';

/**
 * A template key made readable: `inventory_report_submitted` → "Inventory
 * report submitted".
 *
 * The labels come from the backend's `EmailTemplate` registry, so this is only
 * the net under it — for a row whose key the registry has not named (or while
 * the template list is loading, or if it failed). Nothing on the Email Log
 * should ever show a raw identifier. Mirrors `EmailTemplate.label_for`.
 */
export function humaniseTemplateKey(key: string): string {
  const words = key.replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
}

/**
 * The filter's options: the backend's list, then any key on screen it did not
 * carry, so a row's template can always be filtered for.
 */
export function templateOptions(
  registered: EmailTemplateOption[],
  onScreen: string[],
): EmailTemplateOption[] {
  const known = new Set(registered.map(t => t.value));
  const extra = [...new Set(onScreen)]
    .filter(key => key && !known.has(key))
    .sort()
    .map(key => ({ value: key, label: humaniseTemplateKey(key) }));
  return [...registered, ...extra];
}
