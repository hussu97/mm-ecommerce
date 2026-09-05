import { Icon } from '@/components/ui/Icon';
import type { PickupBranch } from '@/lib/types';

/**
 * Which counter to collect from.
 *
 * A pickup order is a promise to walk into a specific shop, so the shop is part
 * of the order rather than something resolved afterwards — before this, the API
 * picked the first branch that could take the job and the customer was shown
 * one hardcoded pin that had no connection to it.
 *
 * Rendered even when there is only one branch. A single row that names the
 * place, its city and a map link is the answer to "where am I going", and
 * hiding it because there was no choice to make is what left people asking.
 */
export function PickupBranchPicker({
  branches, selectedId, onSelect, error, locale, t,
}: {
  branches: PickupBranch[];
  selectedId: string;
  onSelect: (id: string) => void;
  error?: string;
  locale: string;
  t: (k: string, p?: Record<string, string | number>) => string;
}) {
  if (branches.length === 0) {
    return (
      <p className="font-body text-sm text-gray-500">{t('checkout.pickup_branch_unavailable')}</p>
    );
  }

  const isAr = locale === 'ar';

  return (
    <div data-field="pickupBranch" data-field-error={error ? 'true' : undefined}>
      <p className="font-body text-xs text-gray-400 mb-2">{t('checkout.pickup_branch_hint')}</p>
      <div className="space-y-2">
        {branches.map((branch) => {
          const selected = branch.id === selectedId;
          const name = (isAr && branch.name_ar) || branch.name;
          const address = (isAr && branch.address_ar) || branch.address;
          const city = (isAr && branch.city_ar) || branch.city;
          return (
            <div
              key={branch.id}
              className={`border rounded-sm transition-colors ${
                selected ? 'border-primary bg-primary/5'
                  : error ? 'border-red-400' : 'border-gray-200 hover:border-primary/40'
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(branch.id)}
                aria-pressed={selected}
                className="w-full flex items-start gap-3 px-3.5 py-3 text-start"
              >
                <Icon
                  name={selected ? 'radio_button_checked' : 'radio_button_unchecked'}
                  className={`text-xl mt-0.5 ${selected ? 'text-primary' : 'text-gray-400'}`}
                />
                <span className="flex-1 min-w-0">
                  <span className="block font-body text-sm text-gray-800">{name}</span>
                  {address && (
                    <span className="block font-body text-xs text-gray-500 mt-0.5">{address}</span>
                  )}
                  <span className="block font-body text-xs text-gray-400 mt-0.5">
                    {[
                      city,
                      branch.opening_from && branch.opening_to
                        ? `${branch.opening_from} – ${branch.opening_to}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </span>
              </button>
              {branch.maps_url && (
                <a
                  href={branch.maps_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mx-3.5 mb-3 inline-flex items-center gap-1.5 font-body text-xs text-primary hover:underline"
                >
                  <Icon name="place" className="text-sm" />
                  {t('checkout.branch_directions')}
                </a>
              )}
            </div>
          );
        })}
      </div>
      {error && <p className="mt-1.5 text-xs text-red-500 font-body">{error}</p>}
    </div>
  );
}
